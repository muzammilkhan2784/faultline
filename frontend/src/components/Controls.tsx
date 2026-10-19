import { useId } from "react";

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  format?: (value: number) => string;
  hint?: string;
}

function Slider({ label, value, min, max, step, onChange, format, hint }: SliderProps) {
  const id = useId();
  return (
    <div className="min-w-[150px] flex-1">
      <div className="flex items-baseline justify-between">
        <label htmlFor={id} className="label">
          {label}
        </label>
        <span className="font-mono text-xs font-semibold text-ink">{format ? format(value) : value}</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        title={hint}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1.5 h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line accent-[rgb(var(--accent))]"
      />
    </div>
  );
}

export const DepthSlider = (props: { value: number; onChange: (v: number) => void }) => (
  <Slider
    label="Depth"
    min={1}
    max={5}
    step={1}
    format={(v) => `${v} hop${v === 1 ? "" : "s"}`}
    hint="How many calls away from the selected function to draw"
    {...props}
  />
);

export const ConfidenceFilter = (props: { value: number; onChange: (v: number) => void }) => (
  <Slider
    label="Min confidence"
    min={0}
    max={1}
    step={0.05}
    format={(v) => `≥ ${v.toFixed(2)}`}
    hint="Hide edges the resolver was less sure about. 1.0 is an exact lookup; lower means inferred or guessed."
    {...props}
  />
);
