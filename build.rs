use std::{env, fs, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("cargo:rerun-if-changed=server.js");
    println!("cargo:rerun-if-changed=webrtc_contracts.js");
    let source = fs::read_to_string("server.js")?;
    let contracts = fs::read_to_string("webrtc_contracts.js")?;
    let prefix = "const INDEX_HTML = String.raw`";
    let page = source
        .split_once(prefix)
        .ok_or("Node reference has no INDEX_HTML")?
        .1
        .strip_suffix("`;\n")
        .or_else(|| source.split_once(prefix)?.1.strip_suffix("`;\r\n"))
        .ok_or("Node reference INDEX_HTML does not end at EOF")?;
    let inline = contracts.replace("</script", "<\\/script");
    // ECMAScript template literals normalize CRLF source line endings to LF.
    // The included contract is read from disk separately and keeps its bytes.
    let page = page.replace("\r\n", "\n").replace('\r', "\n");
    let mut page = page.replace("${CONTRACT_INLINE_SOURCE}", &inline);
    for name in [
        "RTC_RESULT_GRACE_MS",
        "RTC_CONTROL_SINGLE_MESSAGE_BYTES",
        "RTC_CONTROL_FRAGMENT_PAYLOAD_BYTES",
        "MAX_RTC_CONTROL_FRAGMENT_COUNT",
        "MAX_RTC_REASSEMBLED_CONTROL_BYTES",
    ] {
        page = page.replace(&format!("${{{name}}}"), &format!("__{name}__"));
    }
    fs::write(PathBuf::from(env::var("OUT_DIR")?).join("index.html"), page)?;
    Ok(())
}
