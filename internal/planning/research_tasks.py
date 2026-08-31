#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from internal.common import load_data, dump_json, SKILL_ROOT
from internal.domain import (
    normalize_media_name, resolve_conversion_type, energy_rule_index,
    energy_steam_block, steam_alias_names, match_steam_label,
)
from internal.planning.chapter_rules import evaluate_required_facts, load_chapter_rules, resolve_fact_path, select_active_rules


class _SafeFormat(dict):
    def __missing__(self, key):
        return '{'+key+'}'


def first_product(facts):
    for condition in ('retrofit','design'):
        arr=facts.get('process',{}).get(condition,{}).get('product_streams',[]) or []
        if arr:
            name=arr[0].get('name') or arr[0].get('raw_name') or '目标产品'
            if isinstance(name,str) and name.endswith('产品') and len(name)>2:
                name=name[:-2]
            return name
    return '目标产品'


def sec_status(plan, sec):
    for x in plan:
        if str(x.get('section'))==str(sec):
            return x.get('status')
    return None


def enabled(plan, sec):
    return sec_status(plan,sec) not in (None,'disabled')


def add_task(tasks, task_id, section_id, research_type, subject, questions, queries,
             preferred_sources, minimum_sources=2, target_sections=None,
             requires_official_source=False, purpose=None, prohibited=None,
             geography=None, time_scope=None, rule_id=None):
    task={
      'task_id':task_id,
      'section_id':str(section_id),
      'target_sections':target_sections or [str(section_id)],
      'research_type':research_type,
      'purpose':purpose or '为可研章节提供可核验的外部证据；外部证据不得替代企业内部事实或专业计算。',
      'subject':subject,
      'geography':geography,
      'time_scope':time_scope or '优先采用当前/最近发布资料；涉及历史趋势时覆盖足以支撑判断的合理期间。',
      'questions':questions,
      'suggested_queries':queries,
      'preferred_sources':preferred_sources,
      'minimum_sources':minimum_sources,
      'requires_official_source':requires_official_source,
      'prohibited':prohibited or [
        '不得把搜索摘要或无来源大模型记忆当作证据',
        '不得用公开资料替代企业内部装置、客户、价格、工程条件和财务事实',
        '不得因资料不足而编造数值、技术性能或经济参数'
      ]
    }
    if rule_id: task['rule_id']=rule_id
    tasks.append(task)


def _fmt(value, context):
    if isinstance(value,str):
        return value.format_map(_SafeFormat(context))
    if isinstance(value,list):
        return [_fmt(x,context) for x in value]
    if isinstance(value,dict):
        return {k:_fmt(v,context) for k,v in value.items()}
    return value


def _research_condition_met(research, profile, facts):
    key=research.get('condition_key')
    if not key:
        return True
    if key.startswith('profile.'):
        return resolve_fact_path(profile, key.split('.',1)[1]) not in (None,'',[],{})
    return resolve_fact_path(facts, key) not in (None,'',[],{})


def _add_template_tasks(tasks, rule, profile, facts, product, project_name, location, geography, emitted_ids):
    research=rule.get('research') or {}
    mode=research.get('mode')
    if mode=='none':
        return
    missing=evaluate_required_facts(rule,facts)
    has_research_required=any(x.get('missing_action')=='research_required' for x in missing)
    if mode=='conditional' and not (has_research_required or _research_condition_met(research,profile,facts)):
        return
    context={'product':product,'project_name':project_name,'location':location,'geography':geography,'section_id':rule.get('section_id'),'rule_id':rule.get('rule_id')}
    for idx,tpl in enumerate(research.get('task_templates') or [],start=1):
        data=_fmt(tpl,context)
        task_id=data.get('task_id') or f"R-{str(rule.get('section_id')).replace('.','')}-RULE-{idx:02d}"
        if task_id in emitted_ids:
            continue
        emitted_ids.add(task_id)
        add_task(
          tasks, task_id, data.get('section_id') or rule.get('section_id'),
          data.get('research_type') or 'rule_research',
          data.get('subject') or project_name,
          data.get('questions') or [],
          data.get('suggested_queries') or [],
          data.get('preferred_sources') or [],
          minimum_sources=int(data.get('minimum_sources') or 1),
          target_sections=data.get('target_sections') or research.get('target_sections') or [str(rule.get('section_id'))],
          requires_official_source=bool(data.get('requires_official_source')),
          purpose=data.get('purpose'),
          prohibited=data.get('prohibited'),
          geography=data.get('geography') if 'geography' in data else geography,
          time_scope=data.get('time_scope'),
          rule_id=rule.get('rule_id'))


def accounting_media(profile, facts):
    """Only explicit energy-accounting media participate in conversion-rule lookup.
    Equipment utility clues such as HO/CW are intentionally NOT inferred as energy-accounting media.
    Future document/platform adapters can populate one of these fields.
    """
    vals=[]
    for source in (
        profile.get('energy_accounting_media'),
        facts.get('user',{}).get('energy_accounting_media'),
        facts.get('energy',{}).get('accounting_media'),
    ):
        if isinstance(source,list): vals.extend(source)
    consumption=facts.get('energy',{}).get('consumption')
    if isinstance(consumption,list):
        for item in consumption:
            if isinstance(item,dict):
                vals.append(item.get('medium') or item.get('name') or item.get('energy_type'))
    out=[]
    for x in vals:
        if x is not None and str(x).strip() and str(x).strip() not in out:
            out.append(str(x).strip())
    return out


def _conversion_rules(facts):
    rules=(facts.get('energy') or {}).get('conversion_rules')
    if isinstance(rules,dict) and rules.get('metadata'):
        return rules
    return load_data(SKILL_ROOT/'knowledge/energy_conversion_rules.json')


def missing_conversion_media(profile, facts):
    """Check accounting media against the dual-system rule library of the RESOLVED system.

    Returns (known_media, missing_media, conversion_type). Steam under the oil
    system counts as known (rules built in); its pressure/grade is a separate
    input requirement, not a missing factor.
    """
    rules=_conversion_rules(facts)
    conversion_type,_src=resolve_conversion_type(
        None,
        (facts.get('energy') or {}).get('accounting_standard') or profile.get('energy_accounting_standard'))
    idx=energy_rule_index(rules,conversion_type)
    steam=energy_steam_block(rules,conversion_type)
    steam_aliases=steam_alias_names(steam) if steam else set()
    known=[]; missing=[]
    for media in accounting_media(profile,facts):
        norm=normalize_media_name(media)
        rule=idx.get(norm); note=None
        if rule is None and steam and (norm in steam_aliases or any(a in norm for a in steam_aliases if a)):
            rule=match_steam_label(steam,media) or {'rule_id':(steam.get('rules') or [{}])[0].get('rule_id'),'energy_name':steam.get('energy_name')}
            if match_steam_label(steam,media) is None:
                note='steam_pressure_or_grade_required'
        if rule:
            ename=rule.get('energy_name') or (steam.get('energy_name') if steam else None)
            known.append({'input':media,'rule_id':rule.get('rule_id'),'energy_name':ename,'note':note})
        else: missing.append(media)
    return known, missing, conversion_type


def build_tasks(profile_file, plan_file, facts):
    """Pure function entry: (profile file data, plan file data, facts dict) -> tasks payload."""
    profile=profile_file['project_profile']
    plan=plan_file['chapter_plan']
    product=first_product(facts); project_name=profile.get('project_name') or facts.get('project',{}).get('project_name') or '本项目'
    location=facts.get('user',{}).get('project_location') or profile.get('project_location') or '项目所在地'
    geography=facts.get('user',{}).get('market_geography') or '中国市场为主，必要时补充全球市场'
    tasks=[]
    active_rules=select_active_rules(load_chapter_rules(),plan_file)
    hook_rules={}
    for rule in active_rules:
        research=rule.get('research') or {}
        hook=research.get('policy_hook')
        if hook and research.get('mode') in ('required','conditional'):
            hook_rules.setdefault(hook,[]).append(rule)
    def hook_enabled(name):
        return bool(hook_rules.get(name))
    def hook_rule_id(name):
        rules=hook_rules.get(name) or []
        return rules[0].get('rule_id') if rules else None
    emitted_ids=set()

    for rule in active_rules:
        _add_template_tasks(tasks,rule,profile,facts,product,project_name,location,geography,emitted_ids)

    # 1.1.2: only after the user/project source gives a construction-unit name.
    # Never search for/guess the owner from the project name alone.
    construction_unit=(facts.get('project',{}) or {}).get('construction_unit') or (facts.get('user',{}) or {}).get('construction_unit') or (facts.get('user',{}) or {}).get('owner')
    enterprise_profile=(facts.get('enterprise',{}) or {}).get('basic_profile')
    if hook_enabled('enterprise_profile_112') and construction_unit and not enterprise_profile and 'R-ENT-0112-01' not in emitted_ids:
        emitted_ids.add('R-ENT-0112-01')
        add_task(tasks,'R-ENT-0112-01','1.1.2','enterprise_profile',construction_unit,
          [
            f'{construction_unit}的公开基本情况、主营业务、主要产品/业务板块和与本项目相关的生产经营背景有哪些可核验信息？',
            '哪些信息来自企业官网、年报/公告或政府公开信息，哪些只是媒体二手描述？',
            '如公开资料不足，应明确缺口，不得推断未披露的产能、经营数据或装置情况。'
          ],
          [f'{construction_unit} 官网 主营业务',f'{construction_unit} 年报 公告',f'{construction_unit} 企业 基本情况'],
          ['建设单位官方网站/官方公众号等一手企业资料','上市公司年报/公告（如适用）','政府/园区/信用公示等可核验公开信息','权威媒体仅作辅助'],
          minimum_sources=1, requires_official_source=True,
          purpose='仅在建设单位名称已经由用户或项目资料明确提供后，补充1.1.2主办单位基本情况。优先取得1个建设单位/政府等一手来源，满足写作需要后立即停止；不得通过搜索反推或猜测建设单位。',
          rule_id=hook_rule_id('enterprise_profile_112'))

    # 1.1.3: industry/policy background is still dynamic external evidence.
    if hook_enabled('policy_industry_context_113') and 'R-POL-001-01' not in emitted_ids:
        emitted_ids.add('R-POL-001-01')
        add_task(tasks,'R-POL-001-01','1.1.3','policy_industry_context',project_name,
          [
            f'与{project_name}所属行业和改造目标直接相关的产业政策、行业规划或技术改造背景有哪些？',
            '哪些外部证据可以作为项目建设必要性的行业背景，哪些只能作为宏观背景？'
          ],
          [f'{project_name} 行业 政策 技术改造',f'{product} 产业 政策 规划',f'{product} 行业 发展'],
          ['国务院/国家部委/地方政府官方网站','权威行业协会公开资料','主管部门/园区公开产业资料'],
          minimum_sources=1, requires_official_source=True,
          purpose='仅补充1.1.3必要的行业/政策背景。优先1个与项目直接相关的官方来源，足以支撑背景后立即停止；项目自身必要性仍必须来自企业目标、现状诊断和项目事实。',
          rule_id=hook_rule_id('policy_industry_context_113'))

    # 1.1.4 / design codes / laws use fixed in-package libraries for current MVP. No routine Web task.

    # Chapter 2: market research.
    if hook_enabled('market_forecast_2') and enabled(plan,'2') and 'R-MKT-002-01' not in emitted_ids:
        emitted_ids.add('R-MKT-002-01')
        add_task(tasks,'R-MKT-002-01','2','market_forecast',product,
          [
            f'{product}的主要用途、下游行业及需求驱动因素是什么？',
            f'{product}在中国及必要的全球范围内，公开可验证的供给、产能、产量、进出口或消费情况有哪些？',
            f'{product}的主要生产企业、竞争格局及新增/退出产能信息有哪些？',
            f'{product}近年价格或价格驱动因素如何变化？如缺少公开连续价格序列，应明确证据不足。',
            '与本改造项目相关的市场机会、风险和不确定性是什么？'
          ],
          [f'{product} 市场 产能 需求 生产企业 中国',f'{product} 价格 下游 应用',f'{product} 进出口 海关',f'{product} 行业 报告 协会'],
          ['政府部门/海关/统计机构公开数据','行业协会或标准组织','上市公司年报、公告、环评/项目公示等一手企业资料','权威研究机构或数据库的可核验公开信息','主流财经/行业媒体仅作辅助'],
          minimum_sources=3, geography=geography,
          time_scope='优先覆盖近3-5年历史变化，并给出未来3-5年趋势判断；不得虚构精确预测值。',
          purpose='形成第2章市场预测的最小充分证据底座；3个独立可靠来源已足以支撑主要判断时必须停止扩展检索。企业客户、订单、历史成交价和销售策略仍由U提供。',
          rule_id=hook_rule_id('market_forecast_2'))

    # 4.1.1 / 4.1.2: only route changes require external technology research.
    if hook_enabled('raw_material_route_review_411') and sec_status(plan,'4.1.1')=='full' and 'R-RAW-0411-01' not in emitted_ids:
        emitted_ids.add('R-RAW-0411-01')
        add_task(tasks,'R-RAW-0411-01','4.1.1','raw_material_route_review',product,
          ['可选原料路线及其适用条件、供应约束有哪些？','不同原料路线对产品质量、工艺复杂度、安全环保和成本的影响有哪些公开证据？'],
          [f'{product} 原料 路线 工艺',f'{product} 原料 供应 技术'],
          ['专利/论文/权威技术机构','生产企业公开技术资料','行业协会'],
          minimum_sources=2,
          purpose='仅在原料路线发生变化时支撑4.1.1路线论证；实际原料来源、价格、供应合同仍由企业提供。',
          rule_id=hook_rule_id('raw_material_route_review_411'))

    if hook_enabled('technology_review_412') and sec_status(plan,'4.1.2')=='full' and 'R-TECH-0412-01' not in emitted_ids:
        emitted_ids.add('R-TECH-0412-01')
        add_task(tasks,'R-TECH-0412-01','4.1.2','technology_review',product,
          ['国内外可公开验证的主流工艺路线有哪些？','各路线的技术成熟度、典型特点、安全环保特征有哪些可靠证据？','主要技术提供方、商业化应用或工程案例有哪些？'],
          [f'{product} 工艺 技术 路线',f'{product} process technology licensor',f'{product} 工艺 专利 工业化',f'{product} 工艺 技术 论文'],
          ['专利/同行评议论文/科研机构','技术许可商正式资料','企业年报/项目环评/政府公示中的商业化装置信息','权威行业协会'],
          minimum_sources=2,
          purpose='支撑4.1.2国内外工艺技术概况；取得2个可靠独立来源且足以说明主流路线/成熟度后停止；不得以公开文献替代本项目PA/EA专业计算。',
          rule_id=hook_rule_id('technology_review_412'))
    # 4.1.3 does not trigger an independent Web benchmark in V0.14.8.
    # The comparison uses project diagnosis / PA-EA / confirmed engineering facts;
    # external technology research, when needed, is already handled by 4.1.2.

    # 5.1, 7.x, 8.1 do not auto-search codes in current MVP. Project-specific conditions come from U/C.

    # 18.2: external schedule benchmarks support LLM narrative drafting.
    # Project-specific durations still come from the implementation schedule,
    # equipment scope and confirmed engineering facts; web evidence must not
    # be used to invent exact project dates.
    _has_schedule=bool((facts.get('implementation_schedule') or {}).get('packages'))
    if (not _has_schedule) and hook_enabled('implementation_schedule_benchmark_182') and (enabled(plan,'18.2') or enabled(plan,'18')) and 'R-IMP-0182-01' not in emitted_ids:
        emitted_ids.add('R-IMP-0182-01')
        add_task(tasks,'R-IMP-0182-01','18.2','implementation_schedule_benchmark','化工装置改造项目实施进度与常规工期基准',
          ['化工装置改造项目通常按项目前期、基础设计、施工图设计、设备采购、土建施工、安装工程、试生产等工作包如何组织？',
           '公开项目资料中可核验的常规工期、关键前置条件和设备采购/施工窗口约束有哪些？',
           '哪些资料只能作为工期组织参考，不能直接替代本项目正式进度计划或设备采购周期？'],
          ['化工装置 改造 项目 实施进度 工期','化工装置 技术改造 设备采购 安装 试生产 工期','石化项目 改造 工程进度 公开资料'],
          ['建设单位/园区/主管部门公开项目批复、招标或施工信息','EPC/设计单位公开项目介绍','上市公司公告或环评/项目公示中的建设周期'],
          minimum_sources=1,
          purpose='为18.2提供1个可核验的常规工期组织参考即可；项目工期仍以设备改造量、施工窗口和企业计划为主，取得足够参考后立即停止检索。',
          rule_id=hook_rule_id('implementation_schedule_benchmark_182'))

    # 10.5: built-in rules first. Only missing explicitly-accounted media trigger Web lookup.
    known_media, missing_media, conversion_type=missing_conversion_media(profile,facts)
    conv_label='折标准煤' if conversion_type=='standard_coal' else '折标准油'
    if hook_enabled('missing_energy_conversion_factor_105') and missing_media and 'R-ENE-105-MISSING-01' not in emitted_ids:
        emitted_ids.add('R-ENE-105-MISSING-01')
        subject='、'.join(missing_media)
        add_task(tasks,'R-ENE-105-MISSING-01','10.5','missing_energy_conversion_factor',subject,
          [f'本项目能耗核算介质 {subject} 在当前内置{conv_label}折标规则库中缺失，应采用什么可核验的{conv_label}折算系数？',
           '该系数的单位、折标口径、适用边界和来源是什么？'],
          [f'{m} {conv_label} 系数 综合能耗 计算' for m in missing_media],
          ['国家/行业能耗计算标准','国家部委或标准发布机构','权威行业技术文件'],
          minimum_sources=1, requires_official_source=True,
          purpose=f'仅补充当前内置折标规则库没有覆盖的能耗核算介质（当前口径：{conv_label}）；已内置的电力、蒸汽等不重复联网。',
          rule_id=hook_rule_id('missing_energy_conversion_factor_105'))

    # 10.6 intentionally has NO benchmark search.

    out={
      'contract_version':'1.0',
      'policy_revision':'1.0',
      'project_id':profile.get('project_id') or facts.get('project',{}).get('project_id'),
      'project_name':profile.get('project_name'),
      'energy_conversion_factor_check':{'known_media':known_media,'missing_media':missing_media,'conversion_type':conversion_type,'library':'knowledge/energy_conversion_rules.json'},
      'tasks':tasks
    }
    return out


def main():
    ap=argparse.ArgumentParser(description='Build dynamic external research tasks for feasibility-study chapters (current policy)')
    ap.add_argument('--profile',required=True); ap.add_argument('--plan',required=True); ap.add_argument('--facts',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args()
    out=build_tasks(load_data(a.profile),load_data(a.plan),load_data(a.facts))
    tasks=out.get('tasks') or []; missing_media=(out.get('energy_conversion_factor_check') or {}).get('missing_media')
    dump_json(out,a.output)
    print(json.dumps({'output':a.output,'tasks':len(tasks),'sections':sorted({s for t in tasks for s in t.get('target_sections',[]) }),'missing_energy_conversion_media':missing_media},ensure_ascii=False))

if __name__=='__main__': main()
