/**
 * JSON 查看器：带语法高亮 + 折叠。
 *
 * 简化版：用 <pre> + 正则高亮。深层嵌套时性能可接受（结果通常 <100KB）。
 */

import { useState } from 'react';

interface JsonViewerProps {
  /** 数据（任意 JSON 可序列化值）。 */
  data: unknown;
  /** 初始展开层级（-1 = 全展开）。 */
  initialExpandDepth?: number;
  /** 容器类名。 */
  className?: string;
}

interface NodeProps {
  value: unknown;
  keyName?: string;
  depth: number;
  expandDepth: number;
}

function classifyValue(v: unknown): string {
  if (v === null) return 'json-null';
  if (typeof v === 'string') return 'json-string';
  if (typeof v === 'number') return 'json-number';
  if (typeof v === 'boolean') return 'json-boolean';
  return '';
}

function JsonNode({ value, keyName, depth, expandDepth }: NodeProps) {
  const [collapsed, setCollapsed] = useState(depth > expandDepth);

  if (value === null) {
    return (
      <div className="pl-4">
        {keyName !== undefined && (
          <span className="json-key">"{keyName}"</span>
        )}
        {keyName !== undefined && <span>: </span>}
        <span className="json-null">null</span>
      </div>
    );
  }

  if (typeof value !== 'object') {
    return (
      <div className="pl-4">
        {keyName !== undefined && <span className="json-key">"{keyName}"</span>}
        {keyName !== undefined && <span>: </span>}
        <span className={classifyValue(value)}>
          {typeof value === 'string' ? `"${value}"` : String(value)}
        </span>
      </div>
    );
  }

  const isArray = Array.isArray(value);
  const entries = isArray
    ? value.map((v, i) => [i, v] as const)
    : Object.entries(value as Record<string, unknown>);
  const isEmpty = entries.length === 0;

  if (isEmpty) {
    return (
      <div className="pl-4">
        {keyName !== undefined && <span className="json-key">"{keyName}"</span>}
        {keyName !== undefined && <span>: </span>}
        <span className="text-text-3">{isArray ? '[]' : '{}'}</span>
      </div>
    );
  }

  return (
    <div className="pl-4">
      {keyName !== undefined && (
        <>
          <button
            className="mr-1 text-text-3 hover:text-text"
            onClick={() => setCollapsed((c) => !c)}
          >
            {collapsed ? '+' : '-'}
          </button>
          <span className="json-key">"{keyName}"</span>
          <span>: </span>
        </>
      )}
      {keyName === undefined && (
        <button
          className="mr-1 text-text-3 hover:text-text"
          onClick={() => setCollapsed((c) => !c)}
        >
          {collapsed ? '+' : '-'}
        </button>
      )}
      <span className="text-text-3">{isArray ? '[' : '{'}</span>
      {collapsed ? (
        <span className="text-text-3">
          {isArray ? ` …${entries.length}项 ]` : ` …${entries.length}项 }`}
        </span>
      ) : (
        <div className="border-l border-border pl-2">
          {entries.map(([k, v]) => (
            <JsonNode
              key={String(k)}
              value={v}
              keyName={isArray ? undefined : String(k)}
              depth={depth + 1}
              expandDepth={expandDepth}
            />
          ))}
        </div>
      )}
      <span className="text-text-3">{isArray ? ']' : '}'}</span>
      {keyName !== undefined && depth > 0 && <span className="text-text-3">,</span>}
    </div>
  );
}

export function JsonViewer({
  data,
  initialExpandDepth = 3,
  className = '',
}: JsonViewerProps) {
  return (
    <pre
      className={`overflow-auto font-mono text-xs leading-relaxed text-text-2 ${className}`}
    >
      <JsonNode
        value={data}
        depth={0}
        expandDepth={initialExpandDepth}
      />
    </pre>
  );
}
