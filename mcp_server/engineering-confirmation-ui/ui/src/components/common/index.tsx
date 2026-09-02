export { SectionCard } from './SectionCard';

/** 元信息格（label + value + 可选高亮色）。 */
export function InfoCell({ label, value, highlight }: {
  label: string;
  value: string | number;
  highlight?: 'green' | 'orange' | 'red';
}) {
  const color =
    highlight === 'green' ? 'text-green' :
    highlight === 'orange' ? 'text-orange' :
    highlight === 'red' ? 'text-red' : 'text-text';
  return (
    <div className="rounded bg-bg px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-text-3 font-medium">{label}</div>
      <div className={`mt-0.5 text-[15px] font-semibold tracking-tight ${color}`}>{value}</div>
    </div>
  );
}
