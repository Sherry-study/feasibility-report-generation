#!/usr/bin/env python3
"""Report-stage consistency gates: cross-chapter consistency, chapter-plan compliance and formal-report safety."""
from __future__ import annotations
import re,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.domain import is_disabled_status, plan_status_map

LONG_INTERNAL_TERMS=['Project Facts','Source Resolver','ResearchProvider','LLMProvider','candidate阶段','工程化拓扑']
SHORT_INTERNAL_TOKENS=['PA','EA','FA','FT']
SCHEME_UNKNOWN_PATTERNS=[r'最终(?:采用)?方案尚未确定',r'最终方案.*未确定',r'当前处于方案比选阶段',r'待最终方案确定',r'取决于最终采用方案']
WORK_STATE_PATTERNS=[r'本阶段',r'将在修订稿',r'待后续',r'当前资料不足',r'现阶段尚未']
FORMAL_BANNED_PATTERNS=[
    (r'(?i)test_only\s*=\s*true|TEST ONLY|TEST PUBLISHER|urn:test:|测试正文|测试证据','TEST_MARKER_LEAK','正式报告不得出现测试标记')
]


def report_visible_text(report_model):
    out=[]
    for sec in (report_model or {}).get('sections') or []:
        if sec.get('heading'): out.append(str(sec['heading']))
        for b in sec.get('blocks') or []:
            if b.get('type') in ('heading','paragraph') and b.get('text'): out.append(str(b['text']))
            elif b.get('type')=='numbered_list': out.extend(str(x) for x in b.get('items') or [])
            elif b.get('type')=='table':
                if b.get('caption'): out.append(str(b['caption']))
                out.extend(str(x) for x in b.get('columns') or [])
                for row in b.get('rows') or []: out.extend(str(x) for x in row if x is not None)
    return out


def draft_visible_text(drafts):
    out=[]
    for d in (drafts or {}).get('drafts') or []:
        for sub in d.get('subsections') or []:
            if sub.get('heading'): out.append(str(sub['heading']))
            out.extend(str(x) for x in sub.get('paragraphs') or [])
    return out


def heading_ids(report_model):
    ids=[]
    def one(text):
        m=re.match(r'^\s*(\d+(?:\.\d+)*)\b',str(text or ''))
        return m.group(1) if m else None
    for sec in (report_model or {}).get('sections') or []:
        sid=one(sec.get('heading'))
        if sid: ids.append(sid)
        for b in sec.get('blocks') or []:
            if b.get('type')=='heading':
                sid=one(b.get('text'))
                if sid: ids.append(sid)
    return ids


def section_matches(plan_section, report_section):
    if plan_section=='10.1-10.6': return report_section.startswith('10.') and report_section.split('.')[1].isdigit() and 1 <= int(report_section.split('.')[1]) <= 6
    return report_section==plan_section or report_section.startswith(plan_section+'.')


def check(facts,report_model=None,drafts=None,chapter_plan=None,strict=False):
    issues=[]; warnings=[]
    adopted=(facts.get('adopted_scheme') or {}).get('status')=='available'
    texts=report_visible_text(report_model)+draft_visible_text(drafts); joined='\n'.join(texts)
    if adopted:
        for pat in SCHEME_UNKNOWN_PATTERNS:
            for m in re.finditer(pat,joined): issues.append({'severity':'high','code':'ADOPTED_SCHEME_STATE_CONFLICT','message':f'已存在全局 adopted_scheme，但报告仍出现“{m.group(0)}”类未定状态。'})
    for term in LONG_INTERNAL_TERMS:
        if term in joined: issues.append({'severity':'high','code':'INTERNAL_TERM_LEAK','message':f'正式报告正文出现内部实现术语：{term}'})
    for token in SHORT_INTERNAL_TOKENS:
        pat=rf'(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])'
        if re.search(pat,joined): issues.append({'severity':'high','code':'INTERNAL_TERM_LEAK','message':f'正式报告正文出现内部实现术语token：{token}'})
    for pat,code,message in FORMAL_BANNED_PATTERNS:
        hits=re.findall(pat,joined)
        if hits: issues.append({'severity':'high','code':code,'message':message,'count':len(hits)})
    work_hits=[]
    for pat in WORK_STATE_PATTERNS: work_hits.extend(re.findall(pat,joined))
    if work_hits: warnings.append({'severity':'medium','code':'WORK_STATE_LANGUAGE_OVERUSE','count':len(work_hits),'message':'正文仍含较多工作状态语言；应优先改为工程条件表述，并把集中缺口放入26.3/26.4。'})
    pmap=plan_status_map(chapter_plan)
    if pmap and report_model:
        ids=heading_ids(report_model)
        for ps,status in pmap.items():
            if is_disabled_status(status):
                leaked=sorted({rid for rid in ids if section_matches(ps,rid)})
                if leaked: issues.append({'severity':'high','code':'CHAPTER_PLAN_VIOLATION','message':f'chapter_plan将{ps}设为{status}，但report_model仍包含该章节。','headings':leaked})
    for x in facts.get('consistency_issues') or []:
        sev=x.get('severity')
        if sev=='high' and strict: issues.append({'severity':'high','code':x.get('code'),'message':x.get('message')})
        elif sev in ('high','medium','low'): warnings.append({'severity':sev,'code':x.get('code'),'message':x.get('message')})
    return {'contract_version':'1.1','status':'failed' if issues else ('warning' if warnings else 'passed'),'issues':issues,'warnings':warnings,'adopted_scheme_checked':adopted,'chapter_plan_checked':bool(pmap),'formal_banned_checked':True}
