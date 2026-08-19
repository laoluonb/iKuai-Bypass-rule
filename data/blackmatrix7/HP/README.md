# HP

本目录由 `upstream-sync-transformer` 自动同步上游规则，并转换为爱快可用的逐行格式。

## 上游

- [blackmatrix7-clash](https://github.com/blackmatrix7/ios_rule_script/tree/master/rule/Clash)

## 规则文件

| 规则名称 | 域名格式 | ISP/IP 格式 |
| --- | --- | --- |
| `HP.list` | [下载](HP.list_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/HP/HP.list_domain.txt) | - |
| `HP.yaml` | [下载](HP.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/HP/HP.yaml_domain.txt) | - |
| `HP_No_Resolve.yaml` | [下载](HP_No_Resolve.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/HP/HP_No_Resolve.yaml_domain.txt) | - |

## 格式说明

- 域名文件：每行一条域名或可用的域名规则值。
- ISP/IP 文件：每行一条 IPv4 或 IPv4/CIDR；没有有效内容时不生成。
- 同一文件内及合并文件均已去重并排序。

本文件由 GitHub Actions 每日自动更新。
