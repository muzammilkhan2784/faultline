import type { SymbolDetail } from "@/api/types";
import { Badge } from "./ui";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line/60 py-2 last:border-0">
      <dt className="label shrink-0">{label}</dt>
      <dd className="min-w-0 break-all text-right font-mono text-xs text-ink">{children}</dd>
    </div>
  );
}

export function InfoPanel({ symbol }: { symbol: SymbolDetail }) {
  return (
    <div>
      {symbol.doc && (
        <blockquote className="mb-3 rounded-lg border-l-2 border-accent/60 bg-raised px-3 py-2 text-xs leading-relaxed text-soft">
          {symbol.doc}
        </blockquote>
      )}
      <div className="mb-3 flex flex-wrap gap-1.5">
        {symbol.is_test && <Badge tone="ok">test</Badge>}
        {symbol.is_entrypoint && <Badge tone="info">entrypoint</Badge>}
        {symbol.is_method && <Badge>method</Badge>}
        {symbol.is_async && <Badge tone="accent">async</Badge>}
        {symbol.is_private && <Badge>private</Badge>}
      </div>
      <dl>
        <Row label="Module">{symbol.module}</Row>
        {symbol.class_fqn && <Row label="Class">{symbol.class_fqn.split(".").slice(-1)[0]}</Row>}
        <Row label="Lines">
          {symbol.line_start}–{symbol.line_end}
        </Row>
        <Row label="Parameters">{symbol.param_count}</Row>
        <Row label="Complexity">{symbol.complexity}</Row>
        <Row label="Called by">{symbol.fan_in} function{symbol.fan_in === 1 ? "" : "s"}</Row>
        <Row label="Calls">{symbol.fan_out} function{symbol.fan_out === 1 ? "" : "s"}</Row>
        {symbol.decorators.length > 0 && (
          <Row label="Decorators">
            <span className="flex flex-col items-end gap-0.5">
              {symbol.decorators.map((d) => (
                <span key={d}>@{d}</span>
              ))}
            </span>
          </Row>
        )}
      </dl>
      <p className="mt-3 break-all rounded-lg bg-raised px-3 py-2 font-mono text-2xs text-soft">{symbol.fqn}</p>
    </div>
  );
}
