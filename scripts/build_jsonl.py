"""
构建 JSONL 诗歌数据集 —— 唐诗 + THU-CCPC 合并

职责：A 数据负责人
参考：四人合作执行计划.md

用法：
    python scripts/build_jsonl.py              # 构建
    python scripts/build_jsonl.py --check      # 构建 + 随机抽查

输出：
    data/processed/{train,valid,test,all}.jsonl
    data/processed/{data_stats,dedup_report}.jsonl
    docs/data_notes.md
"""

import re
import os
import sys
import json
import random
import hashlib
import datetime
from collections import Counter

import opencc


# =============================================================================
# 项目根目录推导 (无论从哪里运行)
# =============================================================================

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(_SCRIPT_DIR, '..'))
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
CCPC_DIR = os.path.join(PROJECT_ROOT, 'CCPC')
TANG_DIR = os.path.join(DATA_DIR, 'tang')
OUTPUT_DIR = os.path.join(DATA_DIR, 'processed')
DOCS_DIR = os.path.join(PROJECT_ROOT, 'docs')


# =============================================================================
# 体裁枚举
# =============================================================================
GENRE_WUJUE = 'WUJUE'   # 五言绝句 (5字×4句)
GENRE_QIJUE = 'QIJUE'   # 七言绝句 (7字×4句)
GENRE_WULV  = 'WULV'    # 五言律诗 (5字×8句)
GENRE_QILV  = 'QILV'    # 七言律诗 (7字×8句)
GENRE_OTHER = 'OTHER'   # 其他


# =============================================================================
# 工具函数
# =============================================================================

def _ensure_utf8():
    """确保控制台和文件 I/O 使用 UTF-8"""
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')


def _clean_paragraph(para):
    """清洗单段诗句: 去除括号注释、数字、英文说明、多余空白, 保留汉字和 □"""
    punc_map = {
        ',': '，', '.': '。', '!': '！', '?': '？',
        ';': '；', ':': '：',
    }
    for eng, chn in punc_map.items():
        para = para.replace(eng, chn)

    para = re.sub(r'（[^）]*）', '', para)
    para = re.sub(r'\([^)]*\)', '', para)
    para = re.sub(r'\{[^}]*\}', '', para)
    para = re.sub(r'《[^》]*》', '', para)
    para = re.sub(r'\[[^\]]*\]', '', para)
    para = re.sub(r'[a-zA-Z0-9\-_]+', '', para)
    para = re.sub(r'\s+', '', para)
    para = re.sub(r'。。', '。', para)
    para = re.sub(r'，，', '，', para)
    return para.strip()


def _unify_punctuation(text):
    """统一中英文标点"""
    punc_map = {
        ',': '，', '.': '。', '!': '！', '?': '？',
        ';': '；', ':': '：',
        '（': '(', '）': ')',
        '【': '[', '】': ']',
    }
    for eng, chn in punc_map.items():
        text = text.replace(eng, chn)
    return text


def _split_paragraph_to_lines(para):
    """按标点拆行并清理行内残留注释"""
    lines = re.split(r'[，。！？、；：]', para)
    cleaned = []
    for l in lines:
        l = l.strip()
        if not l:
            continue
        l = re.sub(r'[（(][^）)]*[）)]?', '', l)
        l = re.sub(r'[）)]', '', l)
        l = re.sub(r'[《》【】]', '', l)
        l = re.sub(r'［］\[\]]', '', l)
        l = l.strip()
        if l:
            cleaned.append(l)
    return cleaned


def _normalize_for_dedup(content):
    """标准化诗歌内容用于去重比较: 纯汉字(去| 去标点 去□ 去空白)"""
    s = content.replace('|', '')
    s = re.sub(r'[，。！？、；：（）""''「」『』【】《》—…·\-]', '', s)
    s = re.sub(r'\s+', '', s)
    s = s.replace('□', '')
    return s.strip()


def _detect_genre(content, source='tang'):
    """检测诗歌体裁

    规则:
      - 按 | 分割, 标点不计入字数, □ 计一字位
      - CCPC 已知为绝句, 只区分五言/七言
    """
    lines = content.split('|')
    lines = [l.strip() for l in lines if l.strip()]
    if not lines:
        return GENRE_OTHER

    line_lens = [len(l) for l in lines]
    unique_lens = set(line_lens)
    num_lines = len(lines)

    if source == 'ccpc':
        if len(unique_lens) == 1:
            ll = next(iter(unique_lens))
            if ll == 5 and num_lines == 4:
                return GENRE_WUJUE
            elif ll == 7 and num_lines == 4:
                return GENRE_QIJUE
        return GENRE_OTHER

    if len(unique_lens) == 1:
        ll = next(iter(unique_lens))
        if ll == 5:
            if num_lines == 4:
                return GENRE_WUJUE
            elif num_lines == 8:
                return GENRE_WULV
        elif ll == 7:
            if num_lines == 4:
                return GENRE_QIJUE
            elif num_lines == 8:
                return GENRE_QILV

    return GENRE_OTHER


def _is_high_confidence(record):
    """高置信训练样本判定: 句数正确, 句长一致, 无空句, 无英文/注释, □≤20%"""
    genre = record['genre']
    content = record['content']

    if re.search(r'[a-zA-Z0-9]', content):
        return False
    if re.search(r'[（）(){}【】\[\]《》]', content):
        return False

    lines = content.split('|')
    lines = [l.strip() for l in lines if l.strip()]
    if not lines or any(len(l) == 0 for l in lines):
        return False

    num_lines = len(lines)
    if genre in (GENRE_WUJUE, GENRE_QIJUE):
        if num_lines != 4:
            return False
    elif genre in (GENRE_WULV, GENRE_QILV):
        if num_lines != 8:
            return False
    else:
        return False

    if len(set(len(l) for l in lines)) != 1:
        return False

    total_chars = sum(len(l) for l in lines)
    placeholder_count = content.count('□')
    if total_chars > 0 and placeholder_count / total_chars > 0.2:
        return False

    return True


def _quality_flag_from_content(content):
    """质量标记"""
    text = content.replace('|', '')
    if '□' in text and text.count('□') / max(len(text), 1) > 0.5:
        return 'has_placeholder'
    cjk_count = sum(1 for c in text if '一' <= c <= '鿿')
    if cjk_count < 10:
        return 'too_short'
    if len(text) > 0 and cjk_count / len(text) < 0.6:
        return 'garbled'
    if '□' in text:
        return 'has_placeholder'
    return 'clean'


# =============================================================================
# 数据解析
# =============================================================================

def _parse_tang_to_records(src=None, category='poet.tang'):
    if src is None:
        src = TANG_DIR
    """解析唐诗 JSON → list[record], 保留原始数据"""
    if not os.path.isdir(src):
        print(f'[唐诗] 目录不存在: {src}')
        return []

    cc = opencc.OpenCC('t2s')
    records = []
    seq = 0

    for filename in sorted(os.listdir(src)):
        if not filename.startswith(category):
            continue
        fpath = os.path.join(src, filename)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                poems = json.load(f)
        except Exception as e:
            print(f'  [跳过] {filename}: {e}')
            continue

        for poetry in poems:
            paragraphs = poetry.get('paragraphs', [])
            if not paragraphs:
                continue
            all_lines = []
            for para in paragraphs:
                para_clean = _clean_paragraph(para)
                if not para_clean:
                    continue
                lines = _split_paragraph_to_lines(para_clean)
                all_lines.extend(lines)
            if len(all_lines) < 2:
                continue
            all_lines = [cc.convert(l) for l in all_lines]
            content = '|'.join(all_lines)
            seq += 1
            record = {
                'id': f'tang_{seq:06d}',
                'source': 'tang',
                'content': content,
                'genre': GENRE_OTHER,
                'keywords': [],
                'split': 'train',
                'quality_flag': 'clean',
                'high_confidence': False,
            }
            records.append(record)

    print(f'[唐诗] 解析 {len(records)} 首 (源: {src})')
    return records


def _parse_ccpc_to_records(src=None):
    """解析 CCPC JSONL → list[record], 保留官方 split 划分"""
    if src is None:
        src = CCPC_DIR
    files = [
        ('ccpc_train_v1.0.json', 'train'),
        ('ccpc_valid_v1.0.json', 'valid'),
        ('ccpc_test_v1.0.json',  'test'),
    ]
    records = []

    for fname, split in files:
        fpath = os.path.join(src, fname)
        if not os.path.exists(fpath):
            print(f'  [CCPC] 文件不存在, 跳过: {fpath}')
            continue
        seq = 0
        with open(fpath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = item.get('content', '')
                if not content or '|' not in content:
                    continue
                content = _unify_punctuation(content)
                seq += 1
                kw_text = item.get('keywords', '')
                keywords = [k.strip() for k in kw_text.split() if k.strip()] if kw_text else []
                record = {
                    'id': f'ccpc_{split}_{seq:06d}',
                    'source': 'ccpc',
                    'content': content,
                    'genre': GENRE_OTHER,
                    'keywords': keywords,
                    'split': split,
                    'quality_flag': 'clean',
                    'high_confidence': False,
                }
                records.append(record)
        print(f'  [CCPC/{split}] {fname}: {seq} 首')

    print(f'[CCPC] 解析 {len(records)} 首')
    return records


# =============================================================================
# 去重 + 划分
# =============================================================================

def _dedup_with_split_protection(all_records):
    """跨数据集去重, 保护 CCPC test/valid split

    优先级: CCPC test(0) > CCPC valid(1) > tang(2) > CCPC train(3)
    """
    def _priority(rec):
        sp = rec.get('split', 'train')
        sr = rec.get('source', 'ccpc')
        if sp == 'test':
            return 0
        elif sp == 'valid':
            return 1
        elif sr == 'tang':
            return 2
        else:
            return 3

    hash_map = {}
    for idx, rec in enumerate(all_records):
        key = _normalize_for_dedup(rec['content'])
        if not key:
            continue
        if key not in hash_map:
            hash_map[key] = []
        hash_map[key].append((idx, rec, _priority(rec)))

    keep_indices = set()
    removed_stats = {'test_protected': 0, 'valid_protected': 0, 'train_dedup': 0}

    for key, entries in hash_map.items():
        if len(entries) == 1:
            keep_indices.add(entries[0][0])
            continue
        entries.sort(key=lambda x: x[2])
        best_level = entries[0][2]
        keep_indices.add(entries[0][0])
        for e in entries[1:]:
            idx, rec, prio = e
            sp = rec.get('split', 'train')
            if best_level == 0:
                removed_stats['test_protected'] += 1
            elif best_level == 1:
                if sp == 'train':
                    removed_stats['valid_protected'] += 1
            else:
                removed_stats['train_dedup'] += 1

    deduped = [rec for idx, rec in enumerate(all_records) if idx in keep_indices]
    stats = {
        'before_dedup': len(all_records),
        'after_dedup': len(deduped),
        'removed': len(all_records) - len(deduped),
        'test_protected': removed_stats['test_protected'],
        'valid_protected': removed_stats['valid_protected'],
        'train_dedup': removed_stats['train_dedup'],
    }
    return deduped, stats


def _split_tang_and_merge(tang_records, ccpc_records, seed=42):
    """唐诗 80/10/10 划分 + 与 CCPC 对应集合合并"""
    ccpc_train = [r for r in ccpc_records if r['split'] == 'train']
    ccpc_valid = [r for r in ccpc_records if r['split'] == 'valid']
    ccpc_test  = [r for r in ccpc_records if r['split'] == 'test']

    print(f'\n  CCPC 原始 split: train={len(ccpc_train)} valid={len(ccpc_valid)} test={len(ccpc_test)}')

    rng = random.Random(seed)
    rng.shuffle(tang_records)
    n = len(tang_records)
    n_train = int(n * 0.8)
    n_valid = int(n * 0.1)
    n_test  = n - n_train - n_valid
    if n_test < 0:
        n_test = 0
        n_valid = n - n_train

    tang_train = tang_records[:n_train]
    tang_valid = tang_records[n_train:n_train + n_valid]
    tang_test  = tang_records[n_train + n_valid:]

    for r in tang_train:
        r['split'] = 'train'
    for r in tang_valid:
        r['split'] = 'valid'
    for r in tang_test:
        r['split'] = 'test'

    print(f'  唐诗 split: train={len(tang_train)} valid={len(tang_valid)} test={len(tang_test)}')

    train_records = tang_train + ccpc_train
    valid_records = tang_valid + ccpc_valid
    test_records  = tang_test  + ccpc_test

    train_keys = {_normalize_for_dedup(r['content']) for r in train_records if _normalize_for_dedup(r['content'])}
    valid_keys = {_normalize_for_dedup(r['content']) for r in valid_records if _normalize_for_dedup(r['content'])}
    test_keys  = {_normalize_for_dedup(r['content']) for r in test_records if _normalize_for_dedup(r['content'])}

    train_valid_dup = train_keys & valid_keys
    train_test_dup  = train_keys & test_keys
    valid_test_dup  = valid_keys & test_keys

    cross_dup_found = len(train_valid_dup) + len(train_test_dup) + len(valid_test_dup)

    if cross_dup_found > 0:
        print(f'\n  [!] 发现跨 split 重复: train-valid={len(train_valid_dup)}, '
              f'train-test={len(train_test_dup)}, valid-test={len(valid_test_dup)}')
        if train_valid_dup:
            train_records = [r for r in train_records
                             if _normalize_for_dedup(r['content']) not in train_valid_dup]
        if train_test_dup:
            train_records = [r for r in train_records
                             if _normalize_for_dedup(r['content']) not in train_test_dup]
        print(f'  已从 train 移除 {cross_dup_found} 条跨 split 重复')
    else:
        print(f'\n  [OK] 无跨 split 重复')

    split_stats = {
        'train': len(train_records),
        'valid': len(valid_records),
        'test':  len(test_records),
    }
    return train_records, valid_records, test_records, split_stats


# =============================================================================
# 字频映射
# =============================================================================

def _build_char_frequency(records):
    """全局汉字频率统计"""
    freq = Counter()
    for rec in records:
        text = rec['content'].replace('|', '')
        for c in text:
            if '一' <= c <= '鿿':
                freq[c] += 1
    return freq


def _apply_char_mapping(records, min_freq=3):
    """字频映射: □→[PLACEHOLDER]; 低频汉字(≤min_freq)→[UNK]"""
    freq = _build_char_frequency(records)

    total_chars = sum(freq.values())
    unique_chars = len(freq)
    mapped_placeholder = 0
    mapped_unk = 0
    unk_chars = set()

    for rec in records:
        text = rec['content']
        segments = text.split('|')
        new_segments = []
        for seg in segments:
            chars = list(seg)
            mapped = []
            for c in chars:
                if c == '□':
                    mapped.append('[PLACEHOLDER]')
                    mapped_placeholder += 1
                elif '一' <= c <= '鿿' and freq.get(c, 0) <= min_freq:
                    mapped.append('[UNK]')
                    mapped_unk += 1
                    unk_chars.add(c)
                else:
                    mapped.append(c)
            new_segments.append(''.join(mapped))
        rec['content'] = '|'.join(new_segments)

    kept_chars = unique_chars - len(unk_chars)
    kept_ratio = f'{kept_chars / unique_chars * 100:.1f}%' if unique_chars else '0%'
    stats = {
        'total_chars': total_chars,
        'unique_chars': unique_chars,
        'min_freq': min_freq,
        'mapped_placeholder': mapped_placeholder,
        'mapped_unk': mapped_unk,
        'unique_unk_chars': len(unk_chars),
        'kept_chars': kept_chars,
        'kept_ratio': kept_ratio,
    }
    return records, stats


# =============================================================================
# 主流程
# =============================================================================

def build_jsonl_dataset(output_dir=None):
    """构建完整 JSONL 诗歌数据集 (主流程)

    输出 (不覆盖任何源文件):
      data/processed/{train,valid,test,all}.jsonl
      data/processed/{data_stats,dedup_report,review_samples}.jsonl
      docs/data_notes.md
    """
    if output_dir is None:
        output_dir = OUTPUT_DIR
    _ensure_utf8()
    print('=' * 64)
    print('  构建完整诗歌数据集 (JSONL)')
    print('=' * 64)

    # Step 1
    print('\n[1/5] 解析唐诗...')
    tang_records = _parse_tang_to_records()

    # Step 2
    print('\n[2/5] 解析 CCPC...')
    ccpc_records = _parse_ccpc_to_records()

    # Step 3a: 体裁 + 质量
    print('\n[3/5] 体裁检测 + 质量标记...')
    all_records = tang_records + ccpc_records
    genre_counts = Counter()
    for rec in all_records:
        genre = _detect_genre(rec['content'], source=rec.get('source', 'tang'))
        rec['genre'] = genre
        genre_counts[genre] += 1
        rec['quality_flag'] = _quality_flag_from_content(rec['content'])

    print('  体裁分布:')
    for g in ['WUJUE', 'QIJUE', 'WULV', 'QILV', 'OTHER']:
        n = genre_counts.get(g, 0)
        pct = n / len(all_records) * 100
        print(f'    {g}: {n:>7} ({pct:5.1f}%)')

    # Step 3b: 去重
    print('\n  [去重] 跨数据集去重...')
    deduped, dedup_stats = _dedup_with_split_protection(all_records)
    print(f'    去重前: {dedup_stats["before_dedup"]}')
    print(f'    去重后: {dedup_stats["after_dedup"]}')
    print(f'    移除:   {dedup_stats["removed"]}')
    print(f'      - CCPC test 保护: {dedup_stats["test_protected"]}')
    print(f'      - CCPC valid 保护: {dedup_stats["valid_protected"]}')
    print(f'      - train 内部去重:   {dedup_stats["train_dedup"]}')

    tang_deduped = [r for r in deduped if r['source'] == 'tang']
    ccpc_deduped = [r for r in deduped if r['source'] == 'ccpc']
    print(f'    去重后: tang={len(tang_deduped)}  ccpc={len(ccpc_deduped)}')

    # Step 3c: 划分 + 合并
    print('\n  [划分] 唐诗 80/10/10 + 合并...')
    train_records, valid_records, test_records, split_stats = \
        _split_tang_and_merge(tang_deduped, ccpc_deduped)
    print(f'    最终 split: train={split_stats["train"]} '
          f'valid={split_stats["valid"]} test={split_stats["test"]}')

    all_split = train_records + valid_records + test_records

    # Step 3d: 高置信
    print('\n  [高置信] 标记高置信训练样本...')
    hc_train = sum(1 for r in train_records if _is_high_confidence(r))
    hc_valid = sum(1 for r in valid_records if _is_high_confidence(r))
    hc_test  = sum(1 for r in test_records if _is_high_confidence(r))
    for rec in all_split:
        rec['high_confidence'] = _is_high_confidence(rec)
    print(f'    高置信: train={hc_train} valid={hc_valid} test={hc_test}')

    # Step 4: 字频映射
    print('\n[4/5] 字频映射 ([□]>PLACEHOLDER, 低频>UNK)...')
    final_records, map_stats = _apply_char_mapping(all_split, min_freq=3)
    print(f'    总汉字数:   {map_stats["total_chars"]}')
    print(f'    唯一汉字数: {map_stats["unique_chars"]}')
    print(f'    保留汉字数: {map_stats["kept_chars"]} ({map_stats["kept_ratio"]})')
    print(f'    映射 UNK:   {map_stats["mapped_unk"]} 字次 ({map_stats["unique_unk_chars"]} 个)')
    print(f'    映射 PLACEHOLDER: {map_stats["mapped_placeholder"]} 字次')

    # 映射后二次去重: 不同稀有字被映射为同一 [UNK] 后可能变成相同内容
    print('\n  [二次去重] 映射后内容去重...')
    post_dedup_map = {}
    post_dedup_removed = 0
    for rec in final_records:
        key = rec['content']
        if key in post_dedup_map:
            existing = post_dedup_map[key]
            prio_map = {'test': 0, 'valid': 1, 'train': 2}
            if prio_map.get(rec['split'], 9) < prio_map.get(existing['split'], 9):
                post_dedup_map[key] = rec
            post_dedup_removed += 1
        else:
            post_dedup_map[key] = rec
    final_records = list(post_dedup_map.values())
    print(f'    二次去重移除: {post_dedup_removed} 首')
    print(f'    最终总数: {len(final_records)} 首')

    # 更新统计
    split_records = {'train': [], 'valid': [], 'test': []}
    for rec in final_records:
        split_records[rec['split']].append(rec)
    train_records = split_records['train']
    valid_records = split_records['valid']
    test_records = split_records['test']
    split_stats = {k: len(v) for k, v in split_records.items()}

    # Step 5: 输出
    print('\n[5/5] 输出 JSONL 与报告...')
    os.makedirs(output_dir, exist_ok=True)

    split_groups = {
        'train': train_records,
        'valid': valid_records,
        'test':  test_records,
    }
    output_paths = {}
    for split_name, records in split_groups.items():
        out_path = os.path.join(output_dir, f'{split_name}.jsonl')
        output_paths[split_name] = out_path
        with open(out_path, 'w', encoding='utf-8') as f:
            for rec in records:
                out_rec = {
                    'id': rec['id'],
                    'source': rec['source'],
                    'content': rec['content'],
                    'genre': rec['genre'],
                    'keywords': rec['keywords'],
                    'split': rec['split'],
                    'quality_flag': rec['quality_flag'],
                    'high_confidence': rec['high_confidence'],
                }
                f.write(json.dumps(out_rec, ensure_ascii=False) + '\n')
        print(f'    {out_path}  ({len(records)} 首)')

    # 全量
    all_path = os.path.join(output_dir, 'all.jsonl')
    output_paths['all'] = all_path
    with open(all_path, 'w', encoding='utf-8') as f:
        for rec in final_records:
            out_rec = {
                'id': rec['id'],
                'source': rec['source'],
                'content': rec['content'],
                'genre': rec['genre'],
                'keywords': rec['keywords'],
                'split': rec['split'],
                'quality_flag': rec['quality_flag'],
                'high_confidence': rec['high_confidence'],
            }
            f.write(json.dumps(out_rec, ensure_ascii=False) + '\n')
    print(f'    {all_path}  ({len(final_records)} 首)')

    # data_stats.json
    src_counts = Counter(r['source'] for r in final_records)
    final_genre = Counter(r['genre'] for r in final_records)
    qf_counts = Counter(r['quality_flag'] for r in final_records)
    hc_counts = Counter(r['high_confidence'] for r in final_records)

    data_stats = {
        'total_poems': len(final_records),
        'by_source': dict(src_counts.most_common()),
        'by_genre': {g: final_genre.get(g, 0) for g in ['WUJUE', 'QIJUE', 'WULV', 'QILV', 'OTHER']},
        'by_split': dict(split_stats),
        'by_quality_flag': dict(qf_counts.most_common()),
        'by_high_confidence': {str(k): v for k, v in hc_counts.items()},
        'vocab': {
            'total_chars': map_stats['total_chars'],
            'unique_chars': map_stats['unique_chars'],
            'kept_chars': map_stats['kept_chars'],
            'kept_ratio': map_stats['kept_ratio'],
            'mapped_unk_chars': map_stats['unique_unk_chars'],
            'mapped_placeholder': map_stats['mapped_placeholder'],
        },
        'dedup': {
            'before': dedup_stats['before_dedup'],
            'after': dedup_stats['after_dedup'],
            'removed': dedup_stats['removed'],
            'test_protected': dedup_stats['test_protected'],
            'valid_protected': dedup_stats['valid_protected'],
            'train_dedup': dedup_stats['train_dedup'],
        },
    }
    stats_path = os.path.join(output_dir, 'data_stats.json')
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(data_stats, f, ensure_ascii=False, indent=2)
    print(f'    {stats_path}')

    # dedup_report.json
    dedup_report = {
        'summary': {
            'before_dedup': dedup_stats['before_dedup'],
            'after_dedup': dedup_stats['after_dedup'],
            'total_removed': dedup_stats['removed'],
        },
        'breakdown': {
            'ccpc_test_protected': {
                'count': dedup_stats['test_protected'],
                'description': 'CCPC test 中的诗从所有训练和验证来源移除',
            },
            'ccpc_valid_protected': {
                'count': dedup_stats['valid_protected'],
                'description': 'CCPC valid 中的诗从所有训练来源移除',
            },
            'train_internal_dedup': {
                'count': dedup_stats['train_dedup'],
                'description': '训练集内部重复，只保留一条',
            },
        },
        'after_dedup_by_source': {'tang': len(tang_deduped), 'ccpc': len(ccpc_deduped)},
        'note': '去重依据：标准化正文后的纯汉字字符串精确匹配',
    }
    dedup_path = os.path.join(output_dir, 'dedup_report.json')
    with open(dedup_path, 'w', encoding='utf-8') as f:
        json.dump(dedup_report, f, ensure_ascii=False, indent=2)
    print(f'    {dedup_path}')

    # review_samples.jsonl
    review_path = os.path.join(output_dir, 'review_samples.jsonl')
    _export_review_samples(final_records, output_path=review_path, samples_per_genre=20)
    print(f'    {review_path}')

    # 汇总
    print('\n' + '=' * 64)
    print('  构建完成! 最终数据集统计')
    print('=' * 64)
    for src, n in src_counts.most_common():
        print(f'  来源 {src}: {n} 首 ({n/len(final_records)*100:.1f}%)')
    print('\n  体裁分布:')
    for g in ['WUJUE', 'QIJUE', 'WULV', 'QILV', 'OTHER']:
        n = final_genre.get(g, 0)
        if n:
            print(f'    {g}: {n} ({n/len(final_records)*100:.1f}%)')
    print('\n  Split 分布:')
    for s in ['train', 'valid', 'test']:
        n = split_stats[s]
        print(f'    {s}: {n} ({n/len(final_records)*100:.1f}%)')
    print(f'\n  高置信样本: train={hc_train} valid={hc_valid} test={hc_test}')

    # ── □ 缺字统计 ──
    ph_poems = [r for r in final_records if '[PLACEHOLDER]' in r['content']]
    ph_total = sum(r['content'].count('[PLACEHOLDER]') for r in final_records)
    print(f'\n  □ 缺字统计:')
    print(f'    含 □ 诗歌: {len(ph_poems)} 首 ({len(ph_poems)/len(final_records)*100:.2f}%)')
    print(f'    □ 总出现次数: {ph_total} 字次')
    for src in ['tang', 'ccpc']:
        pool = [r for r in ph_poems if r['source'] == src]
        ph = sum(r['content'].count('[PLACEHOLDER]') for r in pool)
        if pool:
            print(f'    {src}: {len(pool)} 首, {ph} 次')
    for g in ['WUJUE', 'QIJUE', 'WULV', 'QILV', 'OTHER']:
        pool = [r for r in ph_poems if r['genre'] == g]
        ph = sum(r['content'].count('[PLACEHOLDER]') for r in pool)
        tg = final_genre.get(g, 0)
        if pool:
            print(f'    {g}: {len(pool)} 首 ({len(pool)/tg*100:.1f}%), {ph} 次')

    print('\n  输出文件:')
    for name, path in output_paths.items():
        print(f'    {path}')
    for extra in [stats_path, dedup_path, review_path]:
        print(f'    {extra}')
    print('\n  词汇映射规则:')
    print('    [□]>[PLACEHOLDER] (可作为输入; target=PLACEHOLDER忽略损失; 生成禁止)')
    print('    低频汉字(<=3次)>[UNK] (默认禁止输出)')
    print('    PAD=0 SOP=2 (禁止出现在正文输出)')
    print('    EOP=3 (必须允许输出)')
    print('=' * 64)

    # 写数据文档
    _write_data_notes(data_dir=output_dir)

    return final_records


# =============================================================================
# 文档生成
# =============================================================================

def _write_data_notes(data_dir=None):
    """写入 docs/data_notes.md"""
    if data_dir is None:
        data_dir = OUTPUT_DIR
    stats_path = os.path.join(data_dir, 'data_stats.json')
    if os.path.exists(stats_path):
        with open(stats_path, encoding='utf-8') as f:
            stats = json.load(f)
    else:
        stats = {'total_poems': 0, 'by_source': {}, 'by_genre': {}, 'by_split': {}}

    total = stats.get('total_poems', 0)
    by_source = stats.get('by_source', {})
    by_genre = stats.get('by_genre', {})
    by_split = stats.get('by_split', {})
    vocab = stats.get('vocab', {})
    dedup_s = stats.get('dedup', {})

    os.makedirs(DOCS_DIR, exist_ok=True)
    docs_dir = DOCS_DIR

    lines = []
    def w(s=''):
        lines.append(s)

    w('# 数据说明 (data_notes.md)')
    w()
    w(f'> 生成日期：{datetime.date.today().isoformat()}')
    w(f'> 基于：四人合作执行计划.md — A：数据负责人')
    w()
    w('## 数据来源')
    w()
    w('### 1. 当前唐诗数据')
    w()
    w('- **位置**: `data/tang/poet.tang.*.json` (58 个文件)')
    w('- **格式**: JSON 数组, 每条含 `author`, `paragraphs`, `title`, `id`')
    w('- **编码**: UTF-8 (原始为繁体中文)')
    w(f'- **规模**: {by_source.get("tang", 0)} 首进入最终集')
    w('- **处理**: 繁体→简体(opencc), 断句→| 分隔, 去注释/数字/英文')
    w()
    w('### 2. THU-CCPC')
    w()
    w('- **位置**: `CCPC/ccpc_{train,valid,test}_v1.0.json`')
    w('- **格式**: JSONL, 每条含 `dynasty`, `author`, `content`, `title`, `keywords`')
    w('- **编码**: UTF-8 (简体中文)')
    w(f'- **规模**: {by_source.get("ccpc", 0)} 首')
    w('- **处理**: 统一标点, 保持官方 train/valid/test 划分')
    w('- **引用**: [THU-CCPC](https://github.com/THUNLP-AIPoet/Datasets/tree/master/CCPC)')
    w('- **许可**: 仅供学术使用, 须注明来源并引用原论文')
    w()
    w('## 清洗规则')
    w()
    w('| # | 规则 | 说明 |')
    w('|---|------|------|')
    w('| 1 | UTF-8 严格读写 | 所有 I/O 使用 encoding=utf-8 |')
    w('| 2 | 去空行和首尾空白 | 每行 strip() 后判空 |')
    w('| 3 | 统一中英文标点 | 英文 ,.!?;: → 中文 ，。！？；： |')
    w('| 4 | 使用 `|` 保存句界 | 每句独立, 无标点干扰 |')
    w('| 5 | 去除注释/数字/英文 | 括号内容、数字、字母 |')
    w('| 6 | 保留合法汉字和真实 □ | □ 占位符保留, 后续→PLACEHOLDER |')
    w('| 7 | 不直接删除生僻字 | 低频字→UNK 而非删除 |')
    w('| 8 | 保留原始数据 | 不覆盖任何源文件 |')
    w()
    w('## 体裁标注')
    w()
    w('| 条件 | 标签 |')
    w('|------|------|')
    w('| 4句 × 5字 | WUJUE (五言绝句) |')
    w('| 4句 × 7字 | QIJUE (七言绝句) |')
    w('| 8句 × 5字 | WULV (五言律诗) |')
    w('| 8句 × 7字 | QILV (七言律诗) |')
    w('| 其他 | OTHER |')
    w()
    w('约定: 标点不计入字数; □ 计一字位; CCPC 已知为绝句只分五/七言')
    w()
    w('## 跨库去重与防泄漏')
    w()
    w('优先级: CCPC test > CCPC valid > tang > CCPC train')
    w(f'- 去重前: {dedup_s.get("before", 0)} 首 → 去重后: {dedup_s.get("after", 0)} 首')
    w(f'- 总移除: {dedup_s.get("removed", 0)} 首')
    w(f'  - CCPC test 保护: {dedup_s.get("test_protected", 0)}')
    w(f'  - CCPC valid 保护: {dedup_s.get("valid_protected", 0)}')
    w(f'  - Train 内部去重: {dedup_s.get("train_dedup", 0)}')
    w()
    w('唐诗按 80/10/10 单独划分后与 CCPC 对应集合合并。三组之间精确重复 = 0。')
    w()
    w('## 数据统计')
    w()
    w(f'- **总诗歌数**: {total}')
    w(f'- **来源分布**: {", ".join(f"{k}={v}" for k, v in by_source.items())}')
    w()
    w('### 体裁分布')
    for g in ['WUJUE', 'QIJUE', 'WULV', 'QILV', 'OTHER']:
        n = by_genre.get(g, 0)
        pct = n / total * 100 if total else 0
        w(f'- **{g}**: {n} ({pct:.1f}%)')
    w()
    w('### Split 分布')
    for s in ['train', 'valid', 'test']:
        n = by_split.get(s, 0)
        pct = n / total * 100 if total else 0
        w(f'- **{s}**: {n} ({pct:.1f}%)')
    w()
    w('### 词汇映射')
    w(f'- 总汉字数: {vocab.get("total_chars", 0)}')
    w(f'- 唯一汉字: {vocab.get("unique_chars", 0)}')
    w(f'- 保留汉字: {vocab.get("kept_chars", 0)} ({vocab.get("kept_ratio", "0%")})')
    w(f'- 映射 UNK: {vocab.get("mapped_unk_chars", 0)} 个低频字')
    w(f'- 映射 PLACEHOLDER: {vocab.get("mapped_placeholder", 0)} 字次')

    # □ 缺字统计
    ph_total = vocab.get("mapped_placeholder", 0)
    if ph_total:
        # 估算含 □ 诗歌数 (从 data_stats 的 quality_flag 获得)
        w()
        w('### □ 缺字统计')
        w(f'- 映射为 PLACEHOLDER 的原始 □: {ph_total} 字次')
        w(f'- quality_flag=has_placeholder: {stats.get("by_quality_flag", {}).get("has_placeholder", "—")} 首')
    w()
    w('## PLACEHOLDER 与 UNK 约定')
    w()
    w('| 标记 | 来源 | 输入 | 目标(损失) | 生成输出 |')
    w('|------|------|------|-----------|---------|')
    w('| PLACEHOLDER | 原始 □ | 允许 | 忽略损失 | 禁止 |')
    w('| UNK | 低频汉字(≤3次) | 允许 | 正常计算 | 默认禁止 |')
    w('| PAD | 填充 | - | 忽略损失 | 禁止 |')
    w('| SOP | 开始符 | - | 忽略 | 禁止 |')
    w('| EOP | 结束符 | - | 正常 | 必须允许 |')
    w()
    w('## 输出文件')
    w()
    for fname in ['train.jsonl', 'valid.jsonl', 'test.jsonl', 'all.jsonl',
                   'data_stats.json', 'dedup_report.json', 'review_samples.jsonl']:
        fpath = os.path.join(data_dir, fname)
        if os.path.exists(fpath):
            size = os.path.getsize(fpath)
            w(f'- `{fpath}` ({size / 1024 / 1024:.1f} MB)')

    w()
    w('## 局限')
    w()
    w('1. 唐诗体裁为形式弱标签, 部分古诗/乐府可能被误标为律诗/绝句')
    w('2. CCPC 关键词为自动提取, 可能存在噪声')
    w('3. 部分唐诗段落含现代校勘注释未能完全去除')
    w('4. □ 映射为 PLACEHOLDER 后丢失了具体缺字位置信息')
    w('5. 低频字(≤3次)统一映射为 UNK, 可能包含部分有意义字符')

    doc_path = os.path.join(docs_dir, 'data_notes.md')
    with open(doc_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'    {doc_path}')
    return doc_path


# =============================================================================
# 随机抽查
# =============================================================================

def sample_check(records, samples_per_genre=20):
    """每类体裁随机抽查 samples_per_genre 首, 打印详情"""
    from collections import defaultdict

    by_genre = defaultdict(list)
    for rec in records:
        by_genre[rec['genre']].append(rec)

    print(f'\n{"=" * 60}')
    print(f'  随机抽查 (每体裁 {samples_per_genre} 首)')
    print(f'{"=" * 60}')

    for genre in ['WUJUE', 'QIJUE', 'WULV', 'QILV', 'OTHER']:
        pool = by_genre.get(genre, [])
        if not pool:
            print(f'\n{genre}: 无样本')
            continue
        sample = random.Random(42).sample(pool, min(samples_per_genre, len(pool)))
        print(f'\n{genre} (共 {len(pool)} 首, 抽查 {len(sample)} 首):')
        for i, rec in enumerate(sample, 1):
            lines = rec['content'].split('|')
            line_info = ' | '.join(f'{l}({len(l)}字)' for l in lines[:4])
            print(f'  {i}. [{rec["id"]}] [{rec["source"]}] {line_info}')
            if len(lines) > 4:
                print(f'     ... 共 {len(lines)} 行')


def _export_review_samples(records, output_path, samples_per_genre=20):
    """导出抽查样本到 JSONL 文件"""
    from collections import defaultdict

    by_genre = defaultdict(list)
    for rec in records:
        by_genre[rec['genre']].append(rec)

    samples = []
    for genre in ['WUJUE', 'QIJUE', 'WULV', 'QILV', 'OTHER']:
        pool = by_genre.get(genre, [])
        if not pool:
            continue
        chosen = random.Random(42).sample(pool, min(samples_per_genre, len(pool)))
        for rec in chosen:
            detail = {
                'id': rec['id'],
                'source': rec['source'],
                'genre': rec['genre'],
                'content': rec['content'],
                'keywords': rec['keywords'],
                'split': rec['split'],
                'quality_flag': rec['quality_flag'],
                'high_confidence': rec['high_confidence'],
                'lines': [{'text': l, 'length': len(l)} for l in rec['content'].split('|')],
            }
            samples.append(detail)

    with open(output_path, 'w', encoding='utf-8') as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + '\n')

    sample_check(records, samples_per_genre)


# =============================================================================
# 命令行入口
# =============================================================================

if __name__ == '__main__':
    _ensure_utf8()

    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        result = build_jsonl_dataset()
        sample_check(result)
    else:
        build_jsonl_dataset()
