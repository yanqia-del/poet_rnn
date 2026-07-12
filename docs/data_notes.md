# 数据说明 (data_notes.md)

> 生成日期：2026-07-12
> 基于：四人合作执行计划.md — A：数据负责人

## 数据来源

### 1. 当前唐诗数据

- **位置**: `data/tang/poet.tang.*.json` (58 个文件)
- **格式**: JSON 数组, 每条含 `author`, `paragraphs`, `title`, `id`
- **编码**: UTF-8 (原始为繁体中文)
- **规模**: 55188 首进入最终集
- **处理**: 繁体→简体(opencc), 断句→| 分隔, 去注释/数字/英文

### 2. THU-CCPC

- **位置**: `CCPC/ccpc_{train,valid,test}_v1.0.json`
- **格式**: JSONL, 每条含 `dynasty`, `author`, `content`, `title`, `keywords`
- **编码**: UTF-8 (简体中文)
- **规模**: 121494 首
- **处理**: 统一标点, 保持官方 train/valid/test 划分
- **引用**: [THU-CCPC](https://github.com/THUNLP-AIPoet/Datasets/tree/master/CCPC)
- **许可**: 仅供学术使用, 须注明来源并引用原论文

## 清洗规则

| # | 规则 | 说明 |
|---|------|------|
| 1 | UTF-8 严格读写 | 所有 I/O 使用 encoding=utf-8 |
| 2 | 去空行和首尾空白 | 每行 strip() 后判空 |
| 3 | 统一中英文标点 | 英文 ,.!?;: → 中文 ，。！？；： |
| 4 | 使用 `|` 保存句界 | 每句独立, 无标点干扰 |
| 5 | 去除注释/数字/英文 | 括号内容、数字、字母 |
| 6 | 保留合法汉字和真实 □ | □ 占位符保留, 后续→PLACEHOLDER |
| 7 | 不直接删除生僻字 | 低频字→UNK 而非删除 |
| 8 | 保留原始数据 | 不覆盖任何源文件 |

## 体裁标注

| 条件 | 标签 |
|------|------|
| 4句 × 5字 | WUJUE (五言绝句) |
| 4句 × 7字 | QIJUE (七言绝句) |
| 8句 × 5字 | WULV (五言律诗) |
| 8句 × 7字 | QILV (七言律诗) |
| 其他 | OTHER |

约定: 标点不计入字数; □ 计一字位; CCPC 已知为绝句只分五/七言

## 跨库去重与防泄漏

优先级: CCPC test > CCPC valid > tang > CCPC train
- 去重前: 184877 首 → 去重后: 176683 首
- 总移除: 8194 首
  - CCPC test 保护: 634
  - CCPC valid 保护: 528
  - Train 内部去重: 7032

唐诗按 80/10/10 单独划分后与 CCPC 对应集合合并。三组之间精确重复 = 0。

## 数据统计

- **总诗歌数**: 176682
- **来源分布**: ccpc=121494, tang=55188

### 体裁分布
- **WUJUE**: 30508 (17.3%)
- **QIJUE**: 104534 (59.2%)
- **WULV**: 14749 (8.3%)
- **QILV**: 8157 (4.6%)
- **OTHER**: 18734 (10.6%)

### Split 分布
- **train**: 147689 (83.6%)
- **valid**: 13497 (7.6%)
- **test**: 15496 (8.8%)

### 词汇映射
- 总汉字数: 6108516
- 唯一汉字: 8715
- 保留汉字: 6820 (78.3%)
- 映射 UNK: 1895 个低频字
- 映射 PLACEHOLDER: 3300 字次

### □ 缺字统计
- 映射为 PLACEHOLDER 的原始 □: 3300 字次
- quality_flag=has_placeholder: 880 首

## PLACEHOLDER 与 UNK 约定

| 标记 | 来源 | 输入 | 目标(损失) | 生成输出 |
|------|------|------|-----------|---------|
| PLACEHOLDER | 原始 □ | 允许 | 忽略损失 | 禁止 |
| UNK | 低频汉字(≤3次) | 允许 | 正常计算 | 默认禁止 |
| PAD | 填充 | - | 忽略损失 | 禁止 |
| SOP | 开始符 | - | 忽略 | 禁止 |
| EOP | 结束符 | - | 正常 | 必须允许 |

## 输出文件

- `C:\Users\cj336\Desktop\AI程序设计\pytorch_peot_rnn-main\data\processed\train.jsonl` (41.7 MB)
- `C:\Users\cj336\Desktop\AI程序设计\pytorch_peot_rnn-main\data\processed\valid.jsonl` (3.8 MB)
- `C:\Users\cj336\Desktop\AI程序设计\pytorch_peot_rnn-main\data\processed\test.jsonl` (4.3 MB)
- `C:\Users\cj336\Desktop\AI程序设计\pytorch_peot_rnn-main\data\processed\all.jsonl` (49.9 MB)
- `C:\Users\cj336\Desktop\AI程序设计\pytorch_peot_rnn-main\data\processed\data_stats.json` (0.0 MB)
- `C:\Users\cj336\Desktop\AI程序设计\pytorch_peot_rnn-main\data\processed\dedup_report.json` (0.0 MB)
- `C:\Users\cj336\Desktop\AI程序设计\pytorch_peot_rnn-main\data\processed\review_samples.jsonl` (0.1 MB)

## 局限

1. 唐诗体裁为形式弱标签, 部分古诗/乐府可能被误标为律诗/绝句
2. CCPC 关键词为自动提取, 可能存在噪声
3. 部分唐诗段落含现代校勘注释未能完全去除
4. □ 映射为 PLACEHOLDER 后丢失了具体缺字位置信息
5. 低频字(≤3次)统一映射为 UNK, 可能包含部分有意义字符