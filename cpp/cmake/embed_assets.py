"""Embed the shared frontend; neither the build nor runtime invokes Rust/Node."""
import argparse
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--assets',required=True,type=Path)
p.add_argument('--output',required=True,type=Path)
p.add_argument('--web-ui',required=True)
a=p.parse_args()
enabled=a.web_ui.upper() in ('ON','1','TRUE')
page=(a.assets/'index.html').read_text(encoding='utf-8') if enabled else ''
icon=(a.assets/'favicon.svg').read_text(encoding='utf-8') if enabled else ''
assert ')SPEEDTEST_ASSET"' not in page+icon
chunks=[page[i:i+4096] for i in range(0,len(page),4096)]
parts=',\n'.join('R"SPEEDTEST_ASSET('+s+')SPEEDTEST_ASSET"' for s in chunks)
source='''#include "speedtest/http.hpp"
#include <array>
namespace speedtest::assets {
bool web_ui(){return '''+str(enabled).lower()+''';}
std::string page(){
 const std::array<std::string_view,'''+str(len(chunks))+'''> parts={'''+parts+'''};
 std::string result;result.reserve('''+str(len(page.encode()))+''');
 for(auto part:parts)result+=part;return result;
}
std::string_view icon(){return R"SPEEDTEST_ASSET('''+icon+''')SPEEDTEST_ASSET";}
}
'''
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(source,encoding='utf-8',newline='\n')
