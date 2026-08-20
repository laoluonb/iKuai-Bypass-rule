# Rule List Sync

每天从 ACL4SSR、Loyalsoldier 和 BlackMatrix7 同步规则内容，并转换为爱快 Bypass 可直接使用的逐行列表：

- 每个上游文件保留原来的目录和文件对应关系。
- 只有包含域名内容时才生成 `*_domain.txt`；只有包含 IPv4/CIDR 时才生成 `*_isp.txt`，不拆分批次。
- `*_domain.txt` 是纯文本规则，每行一条带点的完整域名。
- `*_isp.txt` 是纯 IPv4/IP-CIDR 列表，每行一条，适合爱快 ISP/IP 分流。
- 所有结果逐行输出、去重并排序。
- 自动生成每个同步目录的 `README.md`，列出上游地址、可用文件和 Raw 订阅地址。
- 生成 `data/proxy/` 下的代理总表，自动排除中国域名和中国 IPv4/CIDR，并进行跨源去重。
- 生成 `data/adblock/ad_domain.txt` 去广告总表，合并广告、隐私和劫持域名，去重并排除放行规则。

## 快速开始

```bash
python src/sync_transform.py --config config/config.json
```

本地测试：

```bash
python -m pytest
```

1. 提交项目到 GitHub。
2. 在仓库的 Actions 页面手动运行一次 `Daily upstream sync`，确认输出符合预期。

工作流默认每天北京时间 `03:00`（`19:00 UTC`）运行，也可以手动触发。GitHub Actions 会把 `data/` 下发生变化的生成文件自动提交回仓库。

## 目录结构

```text
data/acl4ssr/
├── Apple_domain.txt
├── Apple_isp.txt
├── Providers/
│   └── Apple_domain.txt
│   └── Apple_isp.txt
├── Providers/Ruleset/
│   └── Apple_domain.txt
│   └── Apple_isp.txt
├── all_domain.txt
└── all_isp.txt

data/v2ray-rules-dat/
├── proxy-list_domain.txt
├── google-cn_domain.txt
└── ...

data/blackmatrix7/
├── README.md
├── 115.list_domain.txt
├── 115.yaml_domain.txt
└── ...

data/proxy/
├── README.md
├── proxy_domain.txt
└── proxy_isp.txt

data/adblock/
├── README.md
└── ad_domain.txt
```

每个文件都保留上游相对目录。

## 转换规则

- `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-FULL` Clash 规则取逗号后的值，经过合法完整域名校验后写入 `_domain.txt`。
- `DOMAIN-KEYWORD`、正则、通配符、URL、路径和其他无法表示为真实域名的格式会跳过。
- 单标签主机名（例如 `localhost`、`google`、`ca`）不写入域名文件。
- `IP`、`IP-CIDR`、`IP-CIDR6` 取逗号后的值；IPv4 和 IPv4/CIDR 写入 `_isp.txt`，IPv6 不写入 ISP 文件。
- 注释、空行和 YAML 结构行（例如 `payload:`）跳过。
- 每个文件内部去重；`all_domain.txt` 和 `all_isp.txt` 会跨文件去重并排序。
- 代理总表会跨所有配置的输入目录去重；IPv4 网段会进行包含关系归并，避免同一地址重复出现。
- 代理总表会用中国域名表和中国 IPv4 网段表过滤国内内容，只保留需要代理的域名和 IPv4/CIDR。
- YAML 规则中的 `- DOMAIN,...` 和 `- IP-CIDR,...` 也能识别。
- 上游删除文件后，下一次运行会根据 manifest 清理对应的旧生成文件；不同文本源即使共用输出目录，也使用各自的 manifest。

### v2ray-rules-dat 文本规则

- 裸域名、`full:example.com`、`domain:example.com` 去掉类型前缀后，只有带点的完整域名才写入 `_domain.txt`。
- `ip:1.2.3.4`、`cidr:1.2.3.0/24` 去掉类型前缀后写入 `_isp.txt`。
- `regexp:...`、关键词、正则表达式和其他未知格式无法转换为爱快域名格式，会跳过；只有合法域名会写入 `_domain.txt`。

## 只同步一个源

```bash
python src/sync_transform.py --only my-source
```

只检查抓取和转换、不写文件：

```bash
python src/sync_transform.py --dry-run
```

## 配置

主要配置位于 `config/config.json`，支持 JSONC 格式，可以使用 `//` 和 `/* */` 中文注释。当前已预置三个源：

- `acl4ssr-clash`：递归同步 ACL4SSR 的 Clash 规则目录。
- `proxy-list`：同步 Loyalsoldier 的 `proxy-list.txt`。
- `google-cn`：同步 Loyalsoldier 的 `google-cn.txt`。
- `gfw`：同步 Loyalsoldier 的 `gfw.txt`，生成完整域名列表。
- `blackmatrix7-clash`：递归同步 BlackMatrix7 的 Clash 规则目录，保留上游目录和文件扩展名。

### 自动生成目录 README

每个同步源的 `output_dir` 及其包含生成规则文件的子目录都会自动生成 `README.md`。说明文件包含：

- 对应的上游链接。
- 当前实际生成的域名文件和 ISP/IP 文件。
- 仓库内相对下载链接和 Raw 订阅链接。
- 域名、IPv4/CIDR、去重和空文件规则说明。

README 由程序管理，目录没有有效规则文件时仍会保留目录根 README；没有有效域名或 ISP 内容的规则文件不会出现在列表中。

### 添加一个文本链接

如果对方提供的是一个 Raw 文本链接，直接在 `sources` 数组末尾添加：

```json
{
  "name": "my-rules",
  "type": "rule_list",
  "url": "https://example.com/rules.txt",
  "output_dir": "data/custom",
  "timeout_seconds": 30
}
```

然后本地测试：

```bash
python src/sync_transform.py --config config/config.json --only my-rules
```

生成文件为：

```text
data/custom/my-rules_domain.txt
data/custom/my-rules_isp.txt
```

提交配置后，GitHub Actions 每天运行时会自动同步这个新链接，不需要额外修改工作流。

### 添加 GitHub 目录

如果链接指向 GitHub 目录，不要填写网页地址，使用 GitHub Contents API：

```json
{
  "name": "my-github-rules",
  "type": "github_directory",
  "api_url": "https://api.github.com/repos/OWNER/REPO/contents/rules?ref=main",
  "path_prefix": "rules",
  "recursive": true,
  "include_extensions": [".list", ".yaml", ".yml"],
  "output_dir": "data/custom-github",
  "merged_domain_output": "data/custom-github/all_domain.txt",
  "merged_isp_output": "data/custom-github/all_isp.txt",
  "timeout_seconds": 30
}
```

GitHub 网页地址需要转换成 `api.github.com/repos/.../contents/...` 的 API 地址；单个文件则直接使用它的 Raw 下载地址，并配置成 `rule_list`。

常用配置项：

- `output_dir`：每个源文件的输出根目录。
- `merged_domain_output`：域名总表路径。
- `merged_isp_output`：IPv4/CIDR 总表路径；没有任何 IPv4/CIDR 时不生成。
- `include_extensions`：要处理的上游文本文件扩展名。
- `exclude_prefixes`：不处理的上游目录前缀。
- `include_source_extension`：设为 `true` 时，把 `.list`、`.yaml` 等源扩展名保留在输出文件名中，避免同名源文件互相覆盖。
- `archive_url`：GitHub 目录的 ZIP 归档地址；配置后使用一次下载读取整个目录，减少 API 请求并避免递归目录触发限流。

### 代理总表配置

`proxy_aggregate` 会在所有普通源同步完成后运行：

```jsonc
{
  "proxy_aggregate": {
    "enabled": true,
    "input_dirs": ["data/acl4ssr", "data/v2ray-rules-dat", "data/blackmatrix7"],
    "output_dir": "data/proxy",
    "domain_output": "data/proxy/proxy_domain.txt",
    "isp_output": "data/proxy/proxy_isp.txt",
    "china_domain_urls": [
      "https://raw.githubusercontent.com/Loyalsoldier/v2ray-rules-dat/release/china-list.txt"
    ],
    "china_isp_urls": [
      "https://raw.githubusercontent.com/Loyalsoldier/geoip/release/text/cn.txt"
    ]
  }
}
```

它会把所有输入目录中的 `*_domain.txt` 和 `*_isp.txt` 合并，去重排序后过滤中国内容；输出为空时自动删除对应文件。

### 去广告总表配置

`adblock_aggregate` 从指定的广告规则文件中生成一份 SmartDNS 可直接读取的纯域名列表：

```jsonc
{
  "adblock_aggregate": {
    "enabled": true,
    "input_files": [
      "data/acl4ssr/BanAD_domain.txt",
      "data/blackmatrix7/Advertising/Advertising.list_domain.txt"
    ],
    "exclude_files": ["data/acl4ssr/UnBan_domain.txt"],
    "output_dir": "data/adblock",
    "domain_output": "data/adblock/ad_domain.txt"
  }
}
```

生成的 `data/adblock/README.md` 包含北京时间更新时间、规则数量、Raw/CDN 订阅链接、SmartDNS 下载设置和自定义配置。

## 安全与限制

- 输出路径必须是项目目录内的相对路径。
- 请求默认超时 30 秒，可按源设置 `timeout_seconds`。
- 可通过 `headers` 配置 `User-Agent` 或上游要求的请求头。
- GitHub Actions 会自动使用内置的 `GITHUB_TOKEN`；本地运行时可设置环境变量 `GITHUB_TOKEN` 或 `GH_TOKEN`，程序会自动加入 GitHub API 请求头。
