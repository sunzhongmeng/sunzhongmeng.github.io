"""Import a self-contained Markdown folder into this notebook (Python 3.10+)."""
import argparse
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
IMAGES = set('.png .jpg .jpeg .gif .svg .webp .bmp .ico .avif .tif .tiff'.split())
CODE = set('.py .pyw .js .mjs .cjs .ts .tsx .jsx .c .h .cpp .hpp .cc .cs .java .go .rs .sh .bash .ps1 .bat .cmd .sql .r .rb .php .lua .v .sv .vhd .vhdl .ipynb .ino .css .html .htm .json .yaml .yml .toml .xml .ini .cfg .cmake'.split())


def rewrite(text, source, destination, mapping):
    """Rewrite Markdown destinations while leaving fenced/inline code unchanged."""
    def link(value):
        # Markdown backslash escapes apply before URL decoding.
        value = re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]^_`{|}~\\])', r'\1', value)
        parsed = urlsplit(value.replace('\\', '/'))
        if parsed.scheme or parsed.netloc:
            if parsed.scheme.lower() == 'file' or re.match(r'^[a-zA-Z]:', value):
                raise ValueError(f'{source}: 不支持本机绝对路径：{value}')
            return value
        if not parsed.path:
            return value
        if parsed.path.startswith('/'):
            raise ValueError(f'{source}: 请将绝对路径改为文件夹内相对路径：{value}')
        target = (source.parent / unquote(parsed.path)).resolve()
        if target not in mapping:
            raise ValueError(f'{source}: 资源缺失、为目录或位于导入文件夹外：{value}')
        relative = Path(os.path.relpath(mapping[target], destination.parent)).as_posix()
        return quote(relative, safe='/') + ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')

    # Keep indices stable so replacements cannot change the meaning of later matches.
    masked = list(text)
    offset, fence = 0, None
    for line in text.splitlines(keepends=True):
        opening = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if fence:
            masked[offset:offset + len(line)] = ' ' * len(line)
            if re.match(r'^\s{0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*$', line):
                fence = None
        elif opening:
            fence = opening[1]
            masked[offset:offset + len(line)] = ' ' * len(line)
        elif line.startswith('    ') or line.startswith('\t'):
            masked[offset:offset + len(line)] = ' ' * len(line)
        offset += len(line)
    view = ''.join(masked)
    for match in re.finditer(r'(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)', view):
        masked[match.start():match.end()] = ' ' * len(match[0])
    view = ''.join(masked)
    if re.search(r'!?(?<!\\)\[\[[^\n]+?\]\]', view):
        raise ValueError(f'{source}: 请先将 Wiki/Obsidian 双括号链接转换为标准 Markdown 链接')
    # The site does not rewrite raw HTML links. Fail instead of silently losing them.
    for tag in re.finditer(r'<[a-zA-Z][^>]*>', view):
        for match in re.finditer(r'\b(?:src|href|srcset|poster)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag[0], re.I):
            value = next(g for g in match.groups() if g is not None)
            parsed = urlsplit(value)
            if (not parsed.scheme and not parsed.netloc and parsed.path) or parsed.scheme == 'file':
                raise ValueError(f'{source}: 请先将本地 HTML 资源链接转换为 Markdown：{value}')
    spans = []
    # Reference definitions, including optional titles on the same/next line.
    for match in re.finditer(r'^ {0,3}\[[^\]\n]+\]:[ \t]*(?:<([^>\n]*)>|([^\s]+))', view, re.M):
        group = 1 if match[1] is not None else 2
        spans.append((match.start(group), match.end(group)))
    # Inline destinations: handle balanced parentheses and angle-bracket paths.
    for match in re.finditer(r'(?<!\\)\]\([ \t\n]*', view):
        start = match.end()
        if start >= len(view):
            continue
        if view[start] == '<':
            end = view.find('>', start + 1)
            if end < 0:
                continue
            spans.append((start + 1, end))
        else:
            end, depth = start, 0
            while end < len(view):
                char = view[end]
                if char == '\\' and end + 1 < len(view):
                    end += 2
                    continue
                if char == '(':
                    depth += 1
                elif char == ')':
                    if depth == 0:
                        break
                    depth -= 1
                elif char.isspace() and depth == 0:
                    break
                end += 1
            spans.append((start, end))
    for start, end in sorted(set(spans), reverse=True):
        text = text[:start] + link(text[start:end]) + text[end:]
    return text


def plan(source, root, category, name):
    source, root = source.resolve(), root.resolve()
    content = root / 'content'
    if not source.is_dir():
        raise ValueError('导入源必须为文件夹')
    if source.is_relative_to(content) or content.is_relative_to(source):
        raise ValueError('导入源不能包含 content，也不能位于 content 内')
    if not name or name in ('.', '..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((' ', '.')):
        raise ValueError('--name 必须为单个合法文件夹名称')
    category_path = Path(category)
    if category_path.is_absolute() or '..' in category_path.parts or ':' in category:
        raise ValueError('--category 必须为 notes 下的相对目录，不能包含 ..')
    files = []
    for folder, dirs, names in os.walk(source, followlinks=False):
        for entry in [Path(folder) / n for n in dirs + names]:
            if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
                raise ValueError(f'不支持符号链接/目录联接：{entry}')
        files.extend(Path(folder) / n for n in names)
    files.sort()
    if not any(p.suffix.lower() == '.md' for p in files):
        raise ValueError('文件夹中没有 Markdown (.md) 笔记')
    mapping = {}
    for path in files:
        suffix = path.suffix.lower()
        kind = 'notes' if suffix == '.md' else 'images' if suffix in IMAGES else 'code' if suffix in CODE or path.name in ('Makefile', 'Dockerfile', 'CMakeLists.txt') else 'attachments'
        base = content / kind
        if kind == 'notes':
            base /= category_path
        target = (base / name / path.relative_to(source)).resolve()
        if kind == 'notes':
            target = target.with_suffix('.md')
        if not target.is_relative_to(content.resolve()):
            raise ValueError(f'目标越界：{target}')
        mapping[path.resolve()] = target
    if len({str(p).casefold() for p in mapping.values()}) != len(mapping):
        raise ValueError('导入文件中存在大小写或扩展名归一化后的目标冲突')
    result = []
    for path in files:
        target = mapping[path.resolve()]
        data = rewrite(path.read_text(encoding='utf-8-sig'), path, target, mapping).encode('utf-8') if path.suffix.lower() == '.md' else path.read_bytes()
        if target.exists() and (not target.is_file() or target.read_bytes() != data):
            raise ValueError(f'目标已存在且内容不同，未写入任何文件：{target}')
        result.append((target, data, target.exists()))
    return result


def main():
    parser = argparse.ArgumentParser(description='导入外部 Markdown 文件夹；默认仅预览，--apply 才复制。原始文件不变。')
    parser.add_argument('source', type=Path, help='包含笔记和全部资源的文件夹（递归扫描）')
    parser.add_argument('--category', default='imported', help='notes 下的分类，默认 imported，可用 electronics/fpga')
    parser.add_argument('--name', help='本批导入的唯一目录名，默认源文件夹名称')
    parser.add_argument('--apply', action='store_true', help='实际复制并写入更新后的 Markdown')
    args = parser.parse_args()
    try:
        result = plan(args.source, ROOT, args.category, args.name or args.source.resolve().name)
        for target, data, exists in result:
            print(f'{"跳过相同" if exists else "新增"}  {target.relative_to(ROOT)}  ({len(data)} bytes)')
        if args.apply:
            created = []
            try:
                for target, data, exists in result:
                    if exists:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as stream:
                        created.append(target)
                        stream.write(data)
            except Exception:
                for target in reversed(created):
                    target.unlink()
                raise
        print(f'{"导入完成" if args.apply else "预览完成（尚未写入，添加 --apply 执行）"}：{len(result)} 个文件。')
    except (ValueError, OSError, UnicodeError) as exc:
        print(f'导入失败：{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
