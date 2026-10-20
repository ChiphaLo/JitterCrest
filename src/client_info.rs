//! Public client geolocation lookup; private addresses are handled by the HTTP layer.
use serde_json::{Value, json};
use std::{
    collections::{HashMap, VecDeque},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

const MAX_BODY: usize = 256 * 1024;
const CACHE_MAX: usize = 2048;
const CACHE_TTL: Duration = Duration::from_secs(600);

#[derive(Clone)]
pub struct ClientInfo {
    client: reqwest::Client,
    cache: Arc<Mutex<Cache>>,
}

#[derive(Default)]
struct Cache {
    entries: HashMap<String, (Instant, Value)>,
    order: VecDeque<String>,
}

impl Cache {
    fn get(&self, ip: &str, now: Instant) -> Option<Value> {
        let (at, body) = self.entries.get(ip)?;
        (now.duration_since(*at) < CACHE_TTL).then(|| body.clone())
    }

    fn set(&mut self, ip: &str, body: Value, now: Instant) {
        // Match the Node Map: updating an existing key does not move it, and an
        // insertion at capacity evicts the oldest key (even when replacing).
        if self.entries.len() >= CACHE_MAX
            && let Some(oldest) = self.order.pop_front()
        {
            self.entries.remove(&oldest);
        }
        if !self.entries.contains_key(ip) {
            self.order.push_back(ip.to_owned());
        }
        self.entries.insert(ip.to_owned(), (now, body));
    }
}

impl ClientInfo {
    pub fn new() -> Self {
        // Node's https.get does not follow redirects. In particular, never
        // forward a visitor's IP to an unreviewed redirect destination.
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .unwrap_or_else(|error| {
                tracing::error!(%error, "failed to initialize lookup client");
                std::process::exit(1)
            });
        Self {
            client,
            cache: Arc::new(Mutex::new(Cache::default())),
        }
    }

    async fn fetch(&self, url: String, timeout: Duration) -> Option<Value> {
        let mut response = self
            .client
            .get(url)
            .timeout(timeout)
            .header("user-agent", "browser-speedtest/1.0")
            .send()
            .await
            .ok()?;
        if response
            .content_length()
            .is_some_and(|len| len > MAX_BODY as u64)
        {
            return None;
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.ok()? {
            if chunk.len() > MAX_BODY - bytes.len() {
                return None;
            }
            bytes.extend_from_slice(&chunk);
        }
        serde_json::from_slice(&bytes).ok()
    }

    pub async fn lookup(&self, ip: &str) -> Value {
        if let Some(body) = self
            .cache
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(ip, Instant::now())
        {
            return body;
        }
        // The HTTP layer only calls this for parsed public IPs. Encode IPv6
        // colons in the path as encodeURIComponent does in the Node reference.
        let path_ip = ip.replace(':', "%3A");
        let primary = self
            .fetch(
                format!("https://ipwho.is/{path_ip}?fields=success,message,country,country_code,region,city,connection"),
                Duration::from_millis(1800),
            )
            .await;
        let connection = primary.as_ref().map(|data| &data["connection"]);
        let mut asn = normalize_asn(connection.map_or(&Value::Null, |c| &c["asn"]));
        let mut as_name = clean_text(first_truthy(connection, "org", "isp"));
        if primary.as_ref().is_none_or(|data| data["success"] != true)
            || asn.is_empty()
            || as_name.is_empty()
        {
            let fallback = self
                .fetch(
                    format!("https://ipinfo.io/{path_ip}/json"),
                    Duration::from_millis(2500),
                )
                .await;
            if let Some(org) = fallback.as_ref().and_then(|data| js_text(&data["org"])) {
                let (fallback_asn, fallback_name) = parse_ipinfo_org(&org);
                if asn.is_empty() {
                    asn = fallback_asn;
                }
                if as_name.is_empty() {
                    as_name = fallback_name;
                }
            }
        }
        let body = build(ip, primary.as_ref(), asn, as_name);
        // No lock is held while awaiting either network request. Concurrent
        // misses can duplicate lookups, as they do in the Node implementation.
        self.cache
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .set(ip, body.clone(), Instant::now());
        body
    }
}

fn js_text(value: &Value) -> Option<String> {
    match value {
        Value::Null | Value::Bool(false) => None,
        Value::Bool(true) => Some("true".into()),
        Value::String(s) if s.is_empty() => None,
        Value::String(s) => Some(s.clone()),
        Value::Number(n) if n.as_f64() == Some(0.0) => None,
        Value::Number(n) => Some(n.to_string()),
        Value::Array(items) => Some(
            items
                .iter()
                .map(|item| js_text(item).unwrap_or_default())
                .collect::<Vec<_>>()
                .join(","),
        ),
        Value::Object(_) => Some("[object Object]".into()),
    }
}

fn clean_text(value: Option<String>) -> String {
    value
        .unwrap_or_default()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn first_truthy(data: Option<&Value>, first: &str, second: &str) -> Option<String> {
    data.and_then(|value| js_text(&value[first]).or_else(|| js_text(&value[second])))
}

fn normalize_asn(value: &Value) -> String {
    let text = js_text(value).unwrap_or_default();
    let Some(start) = text.find(|c: char| c.is_ascii_digit()) else {
        return String::new();
    };
    let digits: String = text[start..]
        .chars()
        .take_while(char::is_ascii_digit)
        .collect();
    format!("AS{digits}")
}

fn parse_ipinfo_org(org: &str) -> (String, String) {
    let mut chars = org.char_indices();
    if chars
        .next()
        .is_some_and(|(_, c)| c.eq_ignore_ascii_case(&'a'))
        && chars
            .next()
            .is_some_and(|(_, c)| c.eq_ignore_ascii_case(&'s'))
    {
        let digit_end = org[2..]
            .find(|c: char| !c.is_ascii_digit())
            .map_or(org.len(), |index| index + 2);
        if digit_end > 2 && org[digit_end..].starts_with(char::is_whitespace) {
            let name = clean_text(Some(org[digit_end..].to_owned()));
            return (
                normalize_asn(&Value::String(org[..digit_end].to_owned())),
                name,
            );
        }
    }
    (String::new(), clean_text(Some(org.to_owned())))
}

fn build(ip: &str, primary: Option<&Value>, asn: String, as_name: String) -> Value {
    if let Some(data) = primary.filter(|data| data["success"] == true) {
        let connection = &data["connection"];
        let primary_asn = normalize_asn(&connection["asn"]);
        let primary_name = clean_text(first_truthy(Some(connection), "org", "isp"));
        let as_name = if primary_name.is_empty() {
            as_name
        } else {
            primary_name
        };
        let isp = clean_text(first_truthy(Some(connection), "isp", "org"));
        json!({"ip":ip,"countryCode":clean_text(js_text(&data["country_code"])).to_uppercase(),
            "country":clean_text(js_text(&data["country"])),"region":clean_text(js_text(&data["region"])),
            "city":clean_text(js_text(&data["city"])),"isp":if isp.is_empty() { &as_name } else { &isp },
            "asn":if primary_asn.is_empty() { &asn } else { &primary_asn },"asName":as_name,
            "source":"ipwho.is","ok":true,"error":""})
    } else {
        let ok = !asn.is_empty() || !as_name.is_empty();
        json!({"ip":ip,"countryCode":"","country":"","region":"","city":"",
            "isp":as_name,"asn":asn,"asName":as_name,"source":"ipwho.is","ok":ok,
            "error":if ok { "" } else { "client info lookup unavailable" }})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn primary_and_fallback_preserve_node_fields() {
        let data = json!({"success":true,"country_code":" us ","country":"United\nStates",
            "region":"California","city":"San Francisco","connection":{"asn":"as 1234 rest", "isp":" ISP \t One ","org":"  "}});
        let body = build("8.8.8.8", Some(&data), String::new(), "Backup Org".into());
        assert_eq!(body["asn"], "AS1234");
        assert_eq!(body["asName"], "Backup Org");
        assert_eq!(body["isp"], "ISP One");
        assert_eq!(body["countryCode"], "US");
        assert_eq!(body["country"], "United States");
        assert_eq!(body["ok"], true);
        let (asn, name) = parse_ipinfo_org("as7890  Fallback  Org ");
        assert_eq!(build("8.8.8.8", None, asn, name)["asn"], "AS7890");
        assert_eq!(
            parse_ipinfo_org("Unprefixed  Org"),
            (String::new(), "Unprefixed Org".into())
        );
        let failed = json!({"success":false,"connection":{"asn":123,"org":"  Name  "}});
        assert_eq!(
            build(
                "8.8.8.8",
                Some(&failed),
                normalize_asn(&failed["connection"]["asn"]),
                clean_text(first_truthy(Some(&failed["connection"]), "org", "isp"))
            )["ok"],
            true
        );
        assert_eq!(
            js_text(&json!({"unexpected":true})),
            Some("[object Object]".into())
        );
        assert_eq!(js_text(&json!(["A", null, 2])), Some("A,,2".into()));
        assert_eq!(
            build("8.8.8.8", None, String::new(), String::new())["ok"],
            false
        );
    }

    #[test]
    fn cache_ttl_and_fifo_capacity() {
        let mut cache = Cache::default();
        let now = Instant::now();
        cache.set("first", json!(1), now);
        assert_eq!(
            cache.get("first", now + CACHE_TTL - Duration::from_millis(1)),
            Some(json!(1))
        );
        assert_eq!(cache.get("first", now + CACHE_TTL), None);
        for i in 0..CACHE_MAX - 1 {
            cache.set(&format!("ip-{i}"), json!(i), now);
        }
        cache.set("last", json!(9), now);
        assert_eq!(cache.entries.len(), CACHE_MAX);
        assert_eq!(cache.order.len(), CACHE_MAX);
        assert_eq!(cache.get("first", now), None);
        assert_eq!(cache.get("last", now), Some(json!(9)));
        cache.set("last", json!(10), now);
        assert_eq!(cache.entries.len(), CACHE_MAX - 1);
        assert_eq!(cache.get("last", now), Some(json!(10)));
    }
}
