# ChinaMax

本目录由 `upstream-sync-transformer` 自动同步上游规则，并转换为爱快可用的逐行格式。

## 上游

- [blackmatrix7-clash](https://github.com/blackmatrix7/ios_rule_script/tree/master/rule/Clash)

## 规则文件

| 规则名称 | 域名格式 | ISP/IP 格式 |
| --- | --- | --- |
| `ChinaMax.list` | [下载](ChinaMax.list_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax.list_domain.txt) | [下载](ChinaMax.list_isp.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax.list_isp.txt) |
| `ChinaMax.yaml` | [下载](ChinaMax.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax.yaml_domain.txt) | - |
| `ChinaMax_Classical.yaml` | [下载](ChinaMax_Classical.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Classical.yaml_domain.txt) | [下载](ChinaMax_Classical.yaml_isp.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Classical.yaml_isp.txt) |
| `ChinaMax_Classical_No_IPv6.yaml` | [下载](ChinaMax_Classical_No_IPv6.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Classical_No_IPv6.yaml_domain.txt) | [下载](ChinaMax_Classical_No_IPv6.yaml_isp.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Classical_No_IPv6.yaml_isp.txt) |
| `ChinaMax_Classical_No_IPv6_No_Resolve.yaml` | [下载](ChinaMax_Classical_No_IPv6_No_Resolve.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Classical_No_IPv6_No_Resolve.yaml_domain.txt) | [下载](ChinaMax_Classical_No_IPv6_No_Resolve.yaml_isp.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Classical_No_IPv6_No_Resolve.yaml_isp.txt) |
| `ChinaMax_Classical_No_Resolve.yaml` | [下载](ChinaMax_Classical_No_Resolve.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Classical_No_Resolve.yaml_domain.txt) | [下载](ChinaMax_Classical_No_Resolve.yaml_isp.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Classical_No_Resolve.yaml_isp.txt) |
| `ChinaMax_Domain.yaml` | [下载](ChinaMax_Domain.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_Domain.yaml_domain.txt) | - |
| `ChinaMax_IP.yaml` | [下载](ChinaMax_IP.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_IP.yaml_domain.txt) | [下载](ChinaMax_IP.yaml_isp.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_IP.yaml_isp.txt) |
| `ChinaMax_IP_No_IPv6.yaml` | - | [下载](ChinaMax_IP_No_IPv6.yaml_isp.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_IP_No_IPv6.yaml_isp.txt) |
| `ChinaMax_No_Resolve.yaml` | [下载](ChinaMax_No_Resolve.yaml_domain.txt) / [Raw](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/blackmatrix7/ChinaMax/ChinaMax_No_Resolve.yaml_domain.txt) | - |

## 格式说明

- 域名文件：每行一条域名或可用的域名规则值。
- ISP/IP 文件：每行一条 IPv4 或 IPv4/CIDR；没有有效内容时不生成。
- 同一文件内及合并文件均已去重并排序。

本文件由 GitHub Actions 每日自动更新。
