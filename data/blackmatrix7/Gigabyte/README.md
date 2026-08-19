# Gigabyte

本目录由 `upstream-sync-transformer` 自动同步上游规则，并转换为爱快可用的逐行格式。

## 上游

- [blackmatrix7-clash](https://github.com/blackmatrix7/ios_rule_script/tree/master/rule/Clash)

## 规则文件

| 规则名称 | 域名格式 | ISP/IP 格式 |
| --- | --- | --- |
| `Gigabyte.list` | [下载](Gigabyte.list_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/Gigabyte/Gigabyte.list_domain.txt) | - |
| `Gigabyte.yaml` | [下载](Gigabyte.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/Gigabyte/Gigabyte.yaml_domain.txt) | - |
| `Gigabyte_No_Resolve.yaml` | [下载](Gigabyte_No_Resolve.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/Gigabyte/Gigabyte_No_Resolve.yaml_domain.txt) | - |

## 格式说明

- 域名文件：每行一条经过校验的真实域名；正则、关键词、通配符和未知格式会跳过。
- ISP/IP 文件：每行一条 IPv4 或 IPv4/CIDR；没有有效内容时不生成。
- 同一文件内及合并文件均已去重并排序。

本文件由 GitHub Actions 每日自动更新。
