#!/usr/bin/env python3
"""Parse Control-M job definitions into job -> command -> code linkage.
Accepts an XML export or a CSV (job_name, command, depends_on, schedule).
Stdlib only.

Usage: scan_jobs.py --export <jobs.xml|jobs.csv> --out <out.json>
"""
import argparse, csv, json, re, xml.etree.ElementTree as ET

FQN_RE = re.compile(r'\b((?:[a-zA-Z_$][\w$]*\.){2,}[A-Z][\w$]*)\b')
SCRIPT_RE = re.compile(r'[\w./\\:-]+\.(?:sh|ksh|bash|bat|cmd|ps1)\b', re.IGNORECASE)
JAR_RE = re.compile(r'[\w./\\:-]+\.jar\b', re.IGNORECASE)
JAVA_MAIN_RE = re.compile(
    r'\bjava\b\s+(?:-[^\s]+\s+(?:[^\s]+\s+)?)*?((?:[a-zA-Z_$][\w$]*\.)+[A-Z][\w$]*)')


def dissect_command(cmd):
    cmd = cmd or ''
    main = None
    m = JAVA_MAIN_RE.search(cmd)
    if m:
        main = m.group(1)
    else:
        for cand in FQN_RE.findall(cmd):
            if '.jar' not in cand and not cand.startswith(('java.', 'javax.', 'org.')):
                main = cand
                break
    return {
        'main_class': main,
        'scripts': sorted(set(SCRIPT_RE.findall(cmd))),
        'jars': sorted(set(JAR_RE.findall(cmd))),
    }


def parse_xml(path):
    tree = ET.parse(path)
    root = tree.getroot()
    jobs = []
    for el in root.iter():
        tag = el.tag.split('}')[-1].upper()
        if tag != 'JOB':
            continue
        attrs = {k.upper(): v for k, v in el.attrib.items()}
        name = (attrs.get('JOBNAME') or attrs.get('NAME') or attrs.get('JOB_NAME')
                or 'UNKNOWN')
        cmd = (attrs.get('CMDLINE') or attrs.get('COMMAND') or attrs.get('CMD')
               or attrs.get('CMDLINE_TEXT') or '')
        host = attrs.get('NODEID') or attrs.get('HOST') or attrs.get('NODE') or ''
        sched = attrs.get('SCHEDULE') or attrs.get('DAYS') or ''
        depends = []
        for child in el.iter():
            ctag = child.tag.split('}')[-1].upper()
            if ctag in ('INCOND', 'IN_COND', 'DEPENDENCY'):
                cattrs = {k.upper(): v for k, v in child.attrib.items()}
                dep = cattrs.get('NAME') or cattrs.get('COND') or cattrs.get('JOB')
                if dep:
                    depends.append(dep)
        rec = {'job_name': name, 'command': cmd, 'host': host,
               'schedule': sched, 'depends_on': sorted(set(depends))}
        rec.update(dissect_command(cmd))
        jobs.append(rec)
    return jobs


def parse_csv(path):
    jobs = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            cmd = row.get('command', '')
            depends = [d.strip() for d in (row.get('depends_on') or '').split(';')
                       if d.strip()]
            rec = {'job_name': row.get('job_name', 'UNKNOWN'), 'command': cmd,
                   'host': row.get('host', ''), 'schedule': row.get('schedule', ''),
                   'depends_on': depends}
            rec.update(dissect_command(cmd))
            jobs.append(rec)
    return jobs


def parse_condition_value(cond):
    """Extract depended-on job names from an Autosys CONDITION cell.

    Handles plain names ('FEED_LANDED'), Autosys expressions like
    's(JOB_A) AND s(JOB_B)', and ';'-separated lists.
    """
    cond = (cond or '').strip()
    if not cond:
        return []
    names = re.findall(r'[sS](?:uccess)?\(\s*([^)]+?)\s*\)', cond)
    if names:
        return [n.strip().strip('"\'') for n in names if n.strip()]
    parts = re.split(r'\bAND\b|\bOR\b|;', cond, flags=re.IGNORECASE)
    return [p.strip().strip('()"\'') for p in parts if p.strip().strip('()"\'')]


def parse_autosys(path):
    """Parse an Autosys job-list CSV with columns
    JOB_ID,JOB_NAME,JOB_DESCRIPTION,COMMAND,CONDITION,PARENT_JOB_NAME,
    TASK_NAME,PARAMETERS.

    One row per condition: rows for the same job are grouped by JOB_NAME,
    exact duplicate rows are dropped, and CONDITION cells are aggregated
    into the job's depends_on list.
    """
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = [{(k or '').strip().upper(): (v or '').strip()
                 for k, v in r.items()} for r in reader]
    n_rows = len(rows)
    seen, uniq = set(), []
    for r in rows:
        key = tuple(sorted(r.items()))
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    n_dupes = n_rows - len(uniq)

    groups, order = {}, []
    for r in uniq:
        name = r.get('JOB_NAME') or r.get('JOB_ID') or 'UNKNOWN'
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(r)

    jobs = []
    for name in order:
        grows = groups[name]

        def first(col):
            for r in grows:
                v = r.get(col, '')
                if v:
                    return v
            return ''

        depends, parents = [], []
        for r in grows:
            for c in parse_condition_value(r.get('CONDITION', '')):
                if c not in depends:
                    depends.append(c)
            p = r.get('PARENT_JOB_NAME', '')
            if p and p not in parents:
                parents.append(p)
        cmds = sorted({r.get('COMMAND', '') for r in grows if r.get('COMMAND', '')})
        if len(cmds) > 1:
            print(f'warning: job {name} has {len(cmds)} distinct COMMAND values; '
                  f'using the first')
        cmd = cmds[0] if cmds else ''
        rec = {'job_name': name,
               'job_id': first('JOB_ID'),
               'description': first('JOB_DESCRIPTION'),
               'command': cmd,
               'host': '', 'schedule': '',
               'depends_on': depends,
               'parent_jobs': parents,
               'task_name': first('TASK_NAME'),
               'parameters': first('PARAMETERS')}
        info = dissect_command(cmd)
        if not info['main_class'] and not info['scripts']:
            info = dissect_command(f"{rec['task_name']} {rec['parameters']}".strip())
        rec.update(info)
        jobs.append(rec)
    print(f'autosys: {n_rows} rows, {n_dupes} exact-duplicate rows dropped, '
          f'{len(jobs)} jobs')
    return jobs


def detect_format(path):
    if path.lower().endswith('.xml'):
        return 'controlm'
    with open(path, newline='', encoding='utf-8-sig') as f:
        header = next(csv.reader([f.readline()]), [])
    cols = {(c or '').strip().upper() for c in header}
    if 'JOB_ID' in cols:
        return 'autosys'
    return 'controlm'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--format', default='auto',
                    choices=['auto', 'controlm', 'autosys'],
                    help='job export format (auto-detected by default)')
    a = ap.parse_args()
    fmt = a.format if a.format != 'auto' else detect_format(a.export)
    if fmt == 'autosys':
        jobs = parse_autosys(a.export)
    elif a.export.lower().endswith('.xml'):
        jobs = parse_xml(a.export)
    else:
        jobs = parse_csv(a.export)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, indent=1)
    linked = sum(1 for j in jobs if j['main_class'] or j['scripts'])
    print(f'parsed {len(jobs)} jobs ({linked} with code linkage) -> {a.out} '
          f'[format={fmt}]')


if __name__ == '__main__':
    main()
