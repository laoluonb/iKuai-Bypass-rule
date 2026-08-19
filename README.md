# Rule List Sync

每天从 ACL4SSR Clash 规则目录和 Loyalsoldier 的 v2ray 规则文本同步内容，并转换为爱快Bypass可直接使用的逐行列表：

- 每个上游文件保留原来的目录和文件对应关系。
- 每个上游文件至少生成一个 `*_domain.txt`；只有包含 IPv4/CIDR 时才生成 `*_isp.txt`，不拆分批次。
- `*_domain.txt` 是纯文本规则，每行一条域名类内容。
- `*_isp.txt` 是纯 IPv4/IP-CIDR 列表，每行一条，适合爱快 ISP/IP 分流。
- 所有结果逐行输出、去重并排序。

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

工作流默认每天 `03:15 UTC` 运行，也可以手动触发。GitHub Actions 会把 `data/` 下发生变化的生成文件自动提交回仓库。

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
```

每个文件都保留上游相对目录。

## 转换规则

- `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-FULL`、`DOMAIN-KEYWORD` 等 Clash 规则都取逗号后的值，写入 `_domain.txt`。
- `IP`、`IP-CIDR`、`IP-CIDR6` 取逗号后的值；IPv4 和 IPv4/CIDR 写入 `_isp.txt`，IPv6 不写入 ISP 文件。
- 注释、空行和 YAML 结构行（例如 `payload:`）跳过。
- 每个文件内部去重；`all_domain.txt` 和 `all_isp.txt` 会跨文件去重并排序。
- YAML 规则中的 `- DOMAIN,...` 和 `- IP-CIDR,...` 也能识别。
- 上游删除文件后，下一次运行会根据 manifest 清理对应的旧生成文件；不同文本源即使共用输出目录，也使用各自的 manifest。

### v2ray-rules-dat 文本规则

- 裸域名、`full:example.com`、`domain:example.com` 去掉类型前缀后写入 `_domain.txt`。
- `ip:1.2.3.4`、`cidr:1.2.3.0/24` 去掉类型前缀后写入 `_isp.txt`。
- `regexp:...` 和其他非 IP 格式去掉类型前缀后写入 `_domain.txt`，保持上游内容可追踪。

## 只同步一个源

```bash
python src/sync_transform.py --only my-source
```

只检查抓取和转换、不写文件：

```bash
python src/sync_transform.py --dry-run
```

## 配置

主要配置位于 `config/config.json`，当前已预置三个源：

- `acl4ssr-clash`：递归同步 ACL4SSR 的 Clash 规则目录。
- `proxy-list`：同步 Loyalsoldier 的 `proxy-list.txt`。
- `google-cn`：同步 Loyalsoldier 的 `google-cn.txt`。

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

## 安全与限制

- 输出路径必须是项目目录内的相对路径。
- 请求默认超时 30 秒，可按源设置 `timeout_seconds`。
- 可通过 `headers` 配置 `User-Agent` 或上游要求的请求头。
- GitHub Actions 会自动使用内置的 `GITHUB_TOKEN`；本地运行时可设置环境变量 `GITHUB_TOKEN` 或 `GH_TOKEN`，程序会自动加入 GitHub API 请求头。
