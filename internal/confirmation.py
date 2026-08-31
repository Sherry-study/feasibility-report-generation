from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

from internal.domain import deep_merge
from internal.common import EXIT_GENERATED, EXIT_NEEDS_CONFIRMATION, SKILL_ROOT, dump_json, load_data
from internal.facts.calculators import calculate_from_facts, energy_conversion_from_facts


def _first_value(obj, keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj and obj.get(key) is not None:
            value = obj.get(key)
            if isinstance(value, dict) and 'value' in value:
                return value.get('value')
            return value
    return None


def _metric(value, unit, basis, source_status=None):
    return {
        'value': value,
        'unit': unit,
        'basis': basis,
        'status': 'available' if value is not None else 'pending_calculation',
        'source_status': source_status or ('available' if value is not None else 'not_available'),
    }


def _economic_summary(facts):
    fa = facts.get('fa') or {}
    investment = fa.get('investment') or facts.get('investment') or {}
    finance = fa.get('finance') or facts.get('finance') or {}
    confirmed = fa.get('confirmed_economic_summary') or {}

    def from_confirmed(key):
        item = confirmed.get(key) or {}
        return item.get('value') if isinstance(item, dict) else item

    total_investment = from_confirmed('total_investment')
    if total_investment is None:
        total_investment = _first_value(investment, [
            'total_investment', 'project_total_investment', 'total_investment_wan',
            'total_investment_10k_cny', 'total_project_investment'
        ])

    annual_profit = from_confirmed('average_annual_profit')
    if annual_profit is None:
        annual_profit = _first_value(finance, [
            'average_annual_profit', 'annual_average_profit', 'average_profit_total',
            'average_annual_profit_total'
        ])

    payback = from_confirmed('static_payback_period')
    if payback is None:
        payback = _first_value(finance, [
            'static_payback_period_including_construction',
            'static_payback_period', 'payback_period_including_construction', 'payback_period'
        ])

    after_tax_irr = from_confirmed('after_tax_irr')
    if after_tax_irr is None:
        after_tax_irr = _first_value(finance, [
            'after_tax_irr', 'irr_after_tax', 'project_investment_irr_after_tax',
            'project_irr_after_tax'
        ])

    inv_status = investment.get('status') if isinstance(investment, dict) else None
    fin_status = finance.get('status') if isinstance(finance, dict) else None
    return {
        'total_investment': _metric(total_investment, '万元', '项目总投资', inv_status),
        'average_annual_profit': _metric(annual_profit, '万元', '年均利润总额', fin_status),
        'static_payback_period': _metric(payback, '年', '含建设期', fin_status),
        'after_tax_irr': _metric(after_tax_irr, '%', '税后 IRR', fin_status),
    }


def _label(obj):
    tag = str(obj.get('tag') or '').strip()
    name = str(obj.get('name') or '').strip()
    if tag and name:
        return f'{tag} {name}'
    return tag or name or str(obj.get('equipment_id') or obj.get('id') or '待确认设备')


def _action_description(row):
    if not isinstance(row, dict):
        return None
    if row.get('confirmed_retrofit_description'):
        return row.get('confirmed_retrofit_description')
    if row.get('scheme_description'):
        return row.get('scheme_description')
    ag = row.get('action_group')
    if ag == 'parallel_base':
        return '原设备本体不改；配套新增并联设备'
    if ag == 'parallel_new':
        return '新增并联设备'
    if ag == 'series_base':
        return '原设备本体不改；配套新增串联设备'
    if ag == 'series_new':
        return '新增串联设备'
    if ag == 'optimal':
        return '设备本体不改；仅调整运行参数'
    if ag == 'other':
        return '设备本体无需改动'
    if ag == 'addition':
        return row.get('scheme_description') or row.get('conclusion') or '原设备本体实施扩容/结构改造'
    if ag == 'parallel':
        return row.get('scheme_description') or row.get('conclusion') or '原设备本体不改；增设并联设备'
    if ag == 'reuse':
        return row.get('scheme_description') or '利用已有设备承担新的工程用途'
    if ag == 'no_retrofit':
        return '设备本体无需改动'
    return row.get('route') or row.get('conclusion')


def _status_for_row(row):
    if row.get('confirmed_retrofit_status'):
        return row.get('confirmed_retrofit_status')
    ag = row.get('action_group')
    cat = row.get('report_category')
    if ag in ('parallel_new', 'series_new') or cat == '新增':
        return '新增'
    if ag in ('addition', 'heat_transfer') or cat == '改造':
        return '改造'
    if ag == 'reuse' or cat == '利旧':
        return '利旧'
    if ag in ('parallel_base', 'parallel'):
        return '无变化（配套新增并联设备）'
    if ag == 'series_base':
        return '无变化（配套新增串联设备）'
    if ag == 'optimal':
        return '无变化（运行参数优化）'
    if ag in ('other', 'no_retrofit') or cat == '无变化':
        return '无变化'
    return '待确认'


def _equipment_summary(facts):
    eq = facts.get('equipment') or {}
    rows = []
    action_rows = []
    for kind in ('tower', 'reactor'):
        for row in ((eq.get(kind) or {}).get('report_rows') or []):
            r = deepcopy(row)
            r['_kind'] = kind
            action_rows.append(r)

    action_by_id = {}
    for r in action_rows:
        rid = str(r.get('equipment_id') or '')
        if rid:
            action_by_id[rid] = r

    objects = {}
    def add_obj(x, source):
        if not isinstance(x, dict):
            return
        rid = str(x.get('id') if x.get('id') is not None else x.get('equipment_id') or '')
        if not rid:
            return
        cur = objects.setdefault(rid, {'equipment_id': rid, 'source_objects': []})
        for k in ('tag', 'name', 'type', 'form'):
            if not cur.get(k) and x.get(k) is not None:
                cur[k] = x.get(k)
        cur['source_objects'].append(source)

    for x in eq.get('object_catalog') or []:
        add_obj(x, 'equipment.object_catalog')
    for x in (((facts.get('process') or {}).get('retrofit') or {}).get('topology') or {}).get('nodes') or []:
        add_obj(x, 'process.retrofit.topology.nodes')
    for x in ((facts.get('process') or {}).get('retrofit') or {}).get('reactors') or []:
        add_obj(x, 'process.retrofit.reactors')
    for x in ((facts.get('process') or {}).get('retrofit') or {}).get('separators') or []:
        add_obj(x, 'process.retrofit.separators')
    for r in action_rows:
        add_obj({'id': r.get('equipment_id'), 'tag': r.get('tag'), 'name': r.get('name'), 'type': r.get('_kind')}, 'equipment.report_rows')

    for rid, obj in objects.items():
        r = action_by_id.get(rid)
        item = {
            'equipment_id': rid,
            'tag': (r or {}).get('confirmed_tag') or (r or {}).get('tag') or obj.get('tag'),
            'name': (r or {}).get('confirmed_name') or (r or {}).get('name') or obj.get('name'),
            'equipment_type': obj.get('type') or (r or {}).get('_kind'),
            'retrofit_status': _status_for_row(r) if r else '当前未识别改造要求',
            'retrofit_required': ((r or {}).get('retrofit_required') if r and (r or {}).get('retrofit_required') is not None else (True if r and _status_for_row(r) in ('新增', '改造', '利旧') else (False if r else None))),
            'action': (r or {}).get('confirmed_action') or (r or {}).get('scheme_type') or (r or {}).get('action_group') or ('none_identified' if not r else None),
            'retrofit_description': _action_description(r) if r else '当前专业结果未识别该设备的改造动作；如后续专业结果变化，应重新确认。',
            'source': ((r or {}).get('_meta') or {}).get('source_type') or ','.join(obj.get('source_objects') or []),
        }
        rows.append(item)

    rows.sort(key=lambda x: (str(x.get('equipment_id')).isdigit() is False, int(x['equipment_id']) if str(x.get('equipment_id')).isdigit() else str(x.get('equipment_id'))))
    return rows


def _retrofit_points(facts, equipment_summary):
    points = []
    seen = set()
    labels = {str(x.get('equipment_id')): _label(x) for x in equipment_summary}
    eq = facts.get('equipment') or {}

    def add(ptype, desc, ids=None, source=None):
        desc = str(desc or '').strip()
        if not desc:
            return
        key = (ptype, desc)
        if key in seen:
            return
        seen.add(key)
        points.append({
            'point_id': f'RP-{len(points)+1:03d}',
            'type': ptype,
            'description': desc,
            'related_equipment_ids': [str(x) for x in (ids or []) if x is not None],
            'source': source,
        })

    for r in ((eq.get('reactor') or {}).get('report_rows') or []):
        rid = str(r.get('equipment_id') or '')
        label = labels.get(rid) or _label({'equipment_id': rid, 'tag': r.get('tag'), 'name': r.get('name')})
        ag = r.get('action_group')
        p = r.get('selected_parameters') or {}
        if ag == 'reuse':
            reused = p.get('reused_from')
            src_label = labels.get(str(reused)) or (f'设备 {reused}' if reused is not None else '现有设备')
            add('reuse', f'{label} 利旧 {src_label}', [rid, reused], 'EA')
        elif ag == 'parallel':
            add('parallel', f'{label}：{r.get("scheme_description") or "增设并联反应器"}', [rid], 'EA')
        elif ag == 'addition':
            add('equipment_retrofit', f'{label}：{r.get("scheme_description") or r.get("conclusion") or "设备扩容/改造"}', [rid], 'EA')

    for r in ((eq.get('tower') or {}).get('report_rows') or []):
        rid = str(r.get('equipment_id') or '')
        label = labels.get(rid) or _label({'equipment_id': rid, 'name': r.get('name')})
        ag = r.get('action_group')
        if ag == 'parallel_base':
            add('parallel', f'{label} 增设并联系列', [rid], 'EA')
        elif ag == 'series_base':
            add('series', f'{label} 增设串联系列', [rid], 'EA')

    adopted = facts.get('adopted_scheme') or {}
    for ch in adopted.get('topology_changes') or []:
        if isinstance(ch, str):
            add('topology_change', ch, [], 'PA')
        elif isinstance(ch, dict):
            desc = ch.get('description') or ch.get('change') or ch.get('name')
            ids = ch.get('equipment_ids') or ch.get('related_equipment_ids') or []
            add(ch.get('type') or 'topology_change', desc, ids, 'PA')
    return points


_PENDING_INPUT_LABELS={
    'construction_unit':'建设单位（主办单位）',
    'project_name':'正式项目名称',
    'annual_operating_hours':'年运行时长',
}


def _pending_user_inputs(facts):
    """未提供的项目基本信息：在确认单中显式列出，避免缺口被用户无感知地跳过。"""
    project=facts.get('project') or {}
    user=facts.get('user') or {}
    pending=[]

    def add(field,current,supply):
        pending.append({
            'field':field,
            'label':_PENDING_INPUT_LABELS.get(field,field),
            'current':current,
            'how_to_supply':supply,
        })

    if not (project.get('construction_unit') or user.get('construction_unit') or user.get('owner')):
        add('construction_unit','未提供（保留缺口）','--construction-unit 传入后重跑阶段①')
    if user.get('annual_operating_hours') in (None,''):
        add('annual_operating_hours','未提供（年化指标将保持缺口）','--annual-operating-hours 传入后重跑阶段①')
    if (project.get('project_name_source') or 'default') in ('workspace_dir','default'):
        add('project_name',f"当前为占位名：{project.get('project_name')}",'--project-name 传入后重跑阶段①')
    return pending


def build_report_confirmation(facts):
    project = facts.get('project') or {}
    adopted = facts.get('adopted_scheme') or {}
    equipment = _equipment_summary(facts)
    payload = {
        'contract_version': '1.0',
        'project_id': project.get('project_id'),
        'project_name': project.get('project_name'),
        'confirmation_status': 'pending',
        'recommended_scheme': {
            'scheme_id': adopted.get('scheme_id'),
            'name': adopted.get('scheme_name'),
            'description': adopted.get('scheme_description'),
            'status': 'available' if adopted.get('status') == 'available' and (adopted.get('scheme_name') or adopted.get('scheme_description')) else 'pending_confirmation',
        },
        'economic_summary': _economic_summary(facts),
        'pending_user_inputs': _pending_user_inputs(facts),
        'equipment_summary': equipment,
        'retrofit_points': _retrofit_points(facts, equipment),
        'confirmation_instructions': {
            'required_action': '请用户确认以上关键事实；如需修正，在 confirmation_response.json 的 changes 中提供修改后值。',
            'financial_metric_basis': {
                'static_payback_period': '含建设期',
                'internal_rate_of_return': '税后 IRR',
            },
            'missing_value_rule': '未形成 FA-06/FA-07 结果时显示待计算，不允许由 LLM 补造经济指标。',
        },
    }
    return payload


def _merge(target, patch):
    return deep_merge(target, patch, copy=True)


def apply_confirmation(facts, confirmation, response=None, confirm_as_is=False):
    response = response or {}
    if not confirm_as_is:
        if response.get('contract_version') not in (None, '1.0'):
            raise ValueError('confirmation_response.contract_version 必须为 1.0')
        if response.get('decision') != 'confirmed':
            raise ValueError('confirmation_response.decision 必须为 confirmed')
        if response.get('project_id') not in (None, confirmation.get('project_id')):
            raise ValueError('confirmation_response.project_id 与当前项目不一致')
    confirmed = deepcopy(confirmation)
    changes = response.get('changes') or {}
    if 'recommended_scheme' in changes:
        if not isinstance(changes['recommended_scheme'], dict):
            raise ValueError('changes.recommended_scheme 必须为 object')
        _merge(confirmed['recommended_scheme'], changes['recommended_scheme'])

    if 'economic_summary' in changes:
        if not isinstance(changes['economic_summary'], dict):
            raise ValueError('changes.economic_summary 必须为 object')
        fixed_basis={
            'total_investment':'项目总投资',
            'average_annual_profit':'年均利润总额',
            'static_payback_period':'含建设期',
            'after_tax_irr':'税后 IRR',
        }
        for key, patch in changes['economic_summary'].items():
            if key not in fixed_basis:
                raise ValueError(f'未知经济指标: {key}')
            if not isinstance(patch, dict):
                raise ValueError(f'changes.economic_summary.{key} 必须为 object')
            if patch.get('basis') not in (None, fixed_basis[key]):
                raise ValueError(f'{key} 的口径已冻结为 {fixed_basis[key]}，不得在确认响应中修改')
            _merge(confirmed['economic_summary'][key], patch)
            confirmed['economic_summary'][key]['basis']=fixed_basis[key]

    if 'equipment_summary' in changes:
        if not isinstance(changes['equipment_summary'], list):
            raise ValueError('changes.equipment_summary 必须为 array')
        current={str(x.get('equipment_id')):x for x in confirmed.get('equipment_summary') or [] if x.get('equipment_id') is not None}
        order=[str(x.get('equipment_id')) for x in confirmed.get('equipment_summary') or [] if x.get('equipment_id') is not None]
        for patch in changes['equipment_summary']:
            if not isinstance(patch,dict) or patch.get('equipment_id') is None:
                raise ValueError('equipment_summary 修正项必须包含 equipment_id')
            rid=str(patch.get('equipment_id'))
            if rid in current:
                _merge(current[rid],patch)
            else:
                current[rid]=deepcopy(patch); order.append(rid)
        confirmed['equipment_summary']=[current[rid] for rid in order]

    if 'retrofit_points' in changes:
        if not isinstance(changes['retrofit_points'], list):
            raise ValueError('changes.retrofit_points 必须为 array')
        confirmed['retrofit_points'] = deepcopy(changes['retrofit_points'])

    # A value explicitly confirmed by the user becomes an available confirmed metric;
    # missing values remain pending and must not be synthesized by the LLM.
    for metric in (confirmed.get('economic_summary') or {}).values():
        if isinstance(metric, dict):
            if metric.get('value') is not None:
                metric['status'] = 'available'
                metric['source_status'] = 'user_confirmed' if response.get('changes') else metric.get('source_status') or 'available'
            else:
                metric['status'] = 'pending_calculation'

    confirmed['confirmation_status'] = 'confirmed'
    confirmed['confirmed_at'] = datetime.now(timezone.utc).isoformat()
    confirmed['confirmed_by'] = response.get('confirmed_by') or ('explicit_cli_confirm_as_is' if confirm_as_is else 'user')
    confirmed['confirmation_note'] = response.get('note')
    hash_body = {k: confirmed.get(k) for k in ('recommended_scheme', 'economic_summary', 'equipment_summary', 'retrofit_points')}
    confirmed['baseline_hash'] = hashlib.sha256(json.dumps(hash_body, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()

    out = deepcopy(facts)
    rs = confirmed.get('recommended_scheme') or {}
    adopted = out.setdefault('adopted_scheme', {})
    if rs.get('scheme_id') is not None:
        adopted['scheme_id'] = rs.get('scheme_id')
    if rs.get('name') is not None:
        adopted['scheme_name'] = rs.get('name')
        adopted['status'] = 'available'
        adopted['selection_status'] = 'adopted'
    if rs.get('description') is not None:
        adopted['scheme_description'] = rs.get('description')

    out.setdefault('fa', {})['confirmed_economic_summary'] = deepcopy(confirmed.get('economic_summary') or {})

    overlays = {str(x.get('equipment_id')): x for x in (confirmed.get('equipment_summary') or []) if x.get('equipment_id') is not None}
    for kind in ('tower', 'reactor'):
        for row in ((out.get('equipment') or {}).get(kind) or {}).get('report_rows') or []:
            ov = overlays.get(str(row.get('equipment_id')))
            if not ov:
                continue
            row['confirmed_retrofit_status'] = ov.get('retrofit_status')
            row['confirmed_action'] = ov.get('action')
            row['confirmed_retrofit_description'] = ov.get('retrofit_description')
            row['confirmed_name'] = ov.get('name')
            row['confirmed_tag'] = ov.get('tag')

    out['confirmed_baseline'] = deepcopy(confirmed)
    out['confirmation'] = {
        'status': 'confirmed',
        'confirmed_at': confirmed.get('confirmed_at'),
        'confirmed_by': confirmed.get('confirmed_by'),
        'baseline_hash': confirmed.get('baseline_hash'),
        'contract_version': confirmed.get('contract_version'),
    }
    return out, confirmed


def render_confirmation_markdown(confirmation):
    def metric_line(label, item):
        item=item or {}
        value=item.get('value')
        text='待计算' if value is None else f"{value} {item.get('unit') or ''}".strip()
        basis=item.get('basis')
        return f"- **{label}**：{text}" + (f"（{basis}）" if basis else '')

    rs=confirmation.get('recommended_scheme') or {}
    lines=[
        '# 可研编制前关键事实确认', '',
        '## 推荐方案',
        f"- **方案名称**：{rs.get('name') or '待确认'}",
        f"- **方案描述**：{rs.get('description') or '待确认'}", '',
    ]
    pending=confirmation.get('pending_user_inputs') or []
    if pending:
        lines += ['## 未提供的项目信息（确认后保留缺口）']
        lines += [f"- **{x.get('label')}**：{x.get('current')}；可经 {x.get('how_to_supply')} 补充" for x in pending]
        lines += ['']
    lines += [
        '## 投资估算 / 经济性摘要',
        metric_line('总投资',(confirmation.get('economic_summary') or {}).get('total_investment')),
        metric_line('年均利润总额',(confirmation.get('economic_summary') or {}).get('average_annual_profit')),
        metric_line('静态投资回收期',(confirmation.get('economic_summary') or {}).get('static_payback_period')),
        metric_line('内部收益率',(confirmation.get('economic_summary') or {}).get('after_tax_irr')), '',
        '## 设备清单',
        '| 设备 | 是否/状态 | 如何改造 |', '|---|---|---|'
    ]
    for x in confirmation.get('equipment_summary') or []:
        label=_label(x).replace('|','/'); status=str(x.get('retrofit_status') or '待确认').replace('|','/'); desc=str(x.get('retrofit_description') or '待确认').replace('|','/').replace('\n',' ')
        lines.append(f'| {label} | {status} | {desc} |')
    lines += ['', '## 改造点']
    points=confirmation.get('retrofit_points') or []
    if points:
        lines += [f"- {x.get('description')}" for x in points]
    else:
        lines.append('- 当前未形成可确认的结构化改造点。')
    lines += ['', '> 请确认以上内容。未形成的经济指标保持“待计算”，不得由大模型补造；上文“未提供的项目信息”经您确认后保留缺口，后续可随时补充并重跑。']
    return '\n'.join(lines)+'\n'


# ===== 阶段②编排区（engineering_confirmation Tool 的进程内实现） =====


def merge_annualization(facts, result):
    # Copied from run_skill.py (do not modify the original file).
    if result.get('status')!='calculated': return facts
    hours=result.get('annual_operating_hours'); facts.setdefault('user',{})['annual_operating_hours']=hours
    facts.setdefault('fa',{})['annual_capacity']={'status':'calculated','annual_operating_hours':hours,**(result.get('annual_capacity') or {})}
    facts['fa']['annual_material_consumption']={'status':'calculated','annual_operating_hours':hours,**(result.get('annual_material_consumption') or {})}
    facts['gaps']=[g for g in facts.get('gaps',[]) if g.get('field')!='annual_operating_hours']
    return facts


def merge_energy_conversion(facts, result):
    """Write the FA energy-conversion result back into facts['fa']['energy_conversion']."""
    if not isinstance(result, dict):
        return facts
    facts.setdefault('fa',{})['energy_conversion']=deepcopy(result)
    return facts


def run_confirmation_stage(args, output_dir):
    """Run stage 2: annualization + merge + confirmation gate/apply.

    Mirrors run_skill.py L110-139. The file pointed to by args.facts is
    rewritten in place with the merged annualization results, exactly like the
    legacy path. Returns (exit_code, summary_dict) without printing.
    """
    out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    facts_path=Path(args.facts).resolve()

    # FA annualization is a real Tool, not hidden inside fact resolution.
    annual=out/'annualization_result.json'
    ar=calculate_from_facts(load_data(facts_path)); dump_json(ar,annual)
    fdata=merge_annualization(load_data(facts_path),ar); dump_json(fdata,facts_path)

    # FA energy conversion is a real deterministic Tool (dual-system rules).
    energy=out/'energy_conversion_result.json'
    er=energy_conversion_from_facts(load_data(facts_path)); dump_json(er,energy)
    fdata=merge_energy_conversion(load_data(facts_path),er); dump_json(fdata,facts_path)

    # Pre-writing key-fact confirmation is a required human gate.
    confirmation=out/'report_confirmation.json'
    confirmation_data=build_report_confirmation(load_data(facts_path)); dump_json(confirmation_data,confirmation)
    confirmation_md=out/'report_confirmation.md'; confirmation_md.write_text(render_confirmation_markdown(confirmation_data),encoding='utf-8')
    confirmation_response=Path(args.confirmation_response).resolve() if args.confirmation_response else None
    if confirmation_response is None and not args.confirm_as_is:
        return EXIT_NEEDS_CONFIRMATION, {
            'status':'needs_confirmation','resume_exit_code':EXIT_NEEDS_CONFIRMATION,'facts':str(facts_path),
            'report_confirmation':str(confirmation),'report_confirmation_markdown':str(confirmation_md),
            'report_confirmation_schema':str(SKILL_ROOT/'schemas/report_confirmation.schema.json'),
            'confirmation_response_schema':str(SKILL_ROOT/'schemas/report_confirmation_response.schema.json'),
            'next_action':'Present recommended scheme, economic summary, full equipment list and retrofit points to the user. After explicit confirmation, rerun with --confirm-as-is or --confirmation-response.'
        }
    response_data=load_data(confirmation_response) if confirmation_response else {}
    try:
        confirmed_facts_data, confirmed_confirmation=apply_confirmation(load_data(facts_path),confirmation_data,response_data,confirm_as_is=args.confirm_as_is)
    except ValueError as exc:
        return EXIT_NEEDS_CONFIRMATION, {
            'status':'confirmation_invalid','resume_exit_code':EXIT_NEEDS_CONFIRMATION,
            'report_confirmation':str(confirmation),'confirmation_response':str(confirmation_response) if confirmation_response else None,
            'error':str(exc),'next_action':'Correct the confirmation response and rerun.'
        }
    confirmed_facts=out/'confirmed_project_facts.json'; dump_json(confirmed_facts_data,confirmed_facts)
    confirmed_confirmation_path=out/'confirmed_report_confirmation.json'; dump_json(confirmed_confirmation,confirmed_confirmation_path)
    return EXIT_GENERATED, {
        'status':'confirmed','resume_exit_code':EXIT_GENERATED,'facts':str(facts_path),'annualization':str(annual),
        'energy_conversion':str(energy),
        'report_confirmation':str(confirmation),'report_confirmation_markdown':str(confirmation_md),
        'confirmed_facts':str(confirmed_facts),'confirmed_report_confirmation':str(confirmed_confirmation_path)
    }
