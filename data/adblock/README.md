# 去广告

## 前言

去广告规则由本项目每日自动同步、转换和去重生成。

规则数据来自互联网公开项目，仅用于 SmartDNS 域名级广告、跟踪和劫持拦截。

## 规则说明

- 输出只包含合法完整域名，每行一条，不包含 Clash 类型前缀、IP、CIDR、正则或通配符。
- 多个上游规则会合并、去重，并排除配置的直连和放行域名。
- DNS 去广告无法处理与正常内容共用同一域名的广告，且可能存在误拦截。

## 规则统计

最后更新时间：`2026-08-31 05:53:02`（北京时间）

| 类型 | 数量（条） |
| --- | ---: |
| DOMAIN | 290105 |
| TOTAL | 290105 |

## SmartDNS

### 下载设置

```text
文件名：ad_domain.txt
保存目录：/etc/smartdns/domain-set
下载地址：https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/adblock/ad_domain.txt
```

### 自定义配置

```conf
# 加载广告域名集
domain-set -name adblock -type list -file /etc/smartdns/domain-set/ad_domain.txt

# 命中广告域名后返回空地址，同时阻止 A 和 AAAA 解析
address /domain-set:adblock/#
```

广告拦截规则应与代理规则同时保留；`address` 负责拦截广告域名，代理域名仍使用 `nameserver /domain-set:proxylist/proxy` 转发到 OpenClash。

## 规则链接

### MAIN 分支（每日更新）

- [下载规则](ad_domain.txt)
- [Raw 订阅](https://raw.githubusercontent.com/laoluonb/iKuai-Bypass-rule/main/data/adblock/ad_domain.txt)

### MAIN 分支 CDN

- [jsDelivr 订阅](https://cdn.jsdelivr.net/gh/laoluonb/iKuai-Bypass-rule@main/data/adblock/ad_domain.txt)

## 包含规则

- `ACL4SSR BanAD`
- `ACL4SSR BanEasyList`
- `ACL4SSR BanEasyListChina`
- `ACL4SSR BanEasyPrivacy`
- `ACL4SSR BanProgramAD`
- `BlackMatrix7 Advertising`

## 排除规则

- `ACL4SSR UnBan`

## 数据来源

- https://github.com/ACL4SSR/ACL4SSR/tree/master/Clash
- https://github.com/blackmatrix7/ios_rule_script/tree/master/rule/Clash/Advertising

感谢各上游规则维护者的持续更新。规则由 GitHub Actions 每天北京时间 `03:00` 自动生成。
