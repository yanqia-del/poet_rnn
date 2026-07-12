# PLACEHOLDER / UNK 约定（给 B：模型负责人）

> 来自 A：数据负责人
> 日期：2026-07-12

## 数据现状

最终数据集 176,683 首已写入 `data/processed/`，字符映射已应用在 `content` 字段中：

| 映射 | 原始字符 | 替换为 | 数量 |
|------|---------|--------|------|
| PLACEHOLDER | `□`（U+25A1） | `[PLACEHOLDER]` | 3,300 字次，899 首诗 |
| UNK | 低频汉字（全语料出现 ≤3 次） | `[UNK]` | 1,895 个唯一字，3,085 字次 |

## 训练约定

| Token | 编码 | 输入 | 损失 (target) | 生成输出 |
|-------|------|------|---------------|---------|
| `PAD` | vocab[0] | — | **忽略** | **禁止** |
| `UNK` | vocab[1] | 允许 | 正常计算 | **默认禁止** |
| `SOP` | vocab[2] | 起始符 | — | **禁止** |
| `EOP` | vocab[3] | — | 正常 | **必须允许** |
| `[PLACEHOLDER]` | 词表中 | 允许 | **忽略损失** | **禁止** |
| `[UNK]` | 词表中 | 允许 | 正常计算 | **默认禁止** |

### 三条硬规则

1. **`target == [PLACEHOLDER]` 时忽略损失** — 不要求模型预测缺字
2. **生成时禁止输出 `[PLACEHOLDER]`** — 模型必须跳过/替换它
3. **`EOP` 必须允许输出** — 正常结束诗歌

## 特殊 token 列表

```text
PAD
UNK
SOP
EOP
[PLACEHOLDER]
[UNK]
GENRE_WUJUE
GENRE_QIJUE
GENRE_WULV
GENRE_QILV
GENRE_OTHER
KW_BEGIN
KW_SEP
KW_END
```

## 词表构建建议

从 `content` 字段按字切分后构建词表，`[PLACEHOLDER]` 和 `[UNK]` 是整体 token（含方括号），不是拆开为 `[` + `PLACEHOLDER` + `]`。建议：

```python
# 示例
vocab = {'PAD': 0, 'UNK': 1, 'SOP': 2, 'EOP': 3}
for text in records:
    for char in text:  # [PLACEHOLDER] 和 [UNK] 是整体
        if char not in vocab:
            vocab[char] = len(vocab)
```

## 数据示例

```json
{
  "content": "寂寞蓬蒿径|喧喧湫隘庐|屡逢长者辙|时引故人车",
  "genre": "WULV"
}
```

含缺字的示例：
```json
{
  "content": "冉冉岁云暮|凄凄[PLACEHOLDER]复[UNK]|[UNK]此岁云暮|[UNK]为[UNK]雨[UNK]模",
  "genre": "OTHER"
}
```

## 验证方法

训练前运行以下检查：

```python
# 禁止特殊 token 出现在生成输出中
FORBIDDEN_OUTPUT = {'PAD', 'SOP', '[PLACEHOLDER]'}
# 默认禁止（可配置）
DEFAULT_FORBIDDEN = {'UNK', '[UNK]'}
# 必须允许
REQUIRED_ALLOWED = {'EOP'}
```

---

以上约定已在 `data/processed/data_stats.json` 和 `docs/data_notes.md` 中记录。
