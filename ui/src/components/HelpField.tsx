import { cloneElement, useId, useState } from "react";
import type { ReactElement } from "react";

export default function HelpField({
  label,
  help,
  children,
}: {
  label: string;
  help: string;
  children: ReactElement<{ "aria-describedby"?: string }>;
}) {
  const id = useId();
  const [dismissed, setDismissed] = useState(false);
  return (
    <div
      className="help-field"
      data-dismissed={dismissed || undefined}
      onMouseEnter={() => setDismissed(false)}
      onFocus={() => setDismissed(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") setDismissed(true);
      }}
    >
      <label>
        {label}
        {cloneElement(children, { "aria-describedby": id })}
      </label>
      <span id={id} role="tooltip" className="field-tooltip">
        {help}
      </span>
    </div>
  );
}
