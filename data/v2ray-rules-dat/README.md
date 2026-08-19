# v2ray-rules-dat

本目录由 `upstream-sync-transformer` 自动同步上游规则，并转换为爱快可用的逐行格式。

## 上游

- [proxy-list](https://raw.githubusercontent.com/Loyalsoldier/v2ray-rules-dat/release/proxy-list.txt)
- [google-cn](https://raw.githubusercontent.com/Loyalsoldier/v2ray-rules-dat/release/google-cn.txt)
- [gfw](https://raw.githubusercontent.com/Loyalsoldier/v2ray-rules-dat/release/gfw.txt)

## 规则文件

| 规则名称 | 域名格式 | ISP/IP 格式 |
| --- | --- | --- |
| `gfw` | [下载](gfw_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/v2ray-rules-dat/gfw_domain.txt) | - |
| `google-cn` | [下载](google-cn_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/v2ray-rules-dat/google-cn_domain.txt) | - |
| `proxy-list` | [下载](proxy-list_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/v2ray-rules-dat/proxy-list_domain.txt) | - |

## 格式说明

- 域名文件：每行一条经过校验的真实域名；正则、关键词、通配符和未知格式会跳过。
- ISP/IP 文件：每行一条 IPv4 或 IPv4/CIDR；没有有效内容时不生成。
- 同一文件内及合并文件均已去重并排序。

本文件由 GitHub Actions 每日自动更新。
