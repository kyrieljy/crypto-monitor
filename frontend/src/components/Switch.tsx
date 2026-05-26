interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
}

export function Switch({ checked, onChange, label }: SwitchProps) {
  return (
    <button
      type="button"
      className={checked ? "switch checked" : "switch"}
      role="switch"
      aria-checked={checked}
      onClick={(event) => {
        event.stopPropagation();
        onChange(!checked);
      }}
    >
      <span aria-hidden="true" />
      {label && <em>{label}</em>}
    </button>
  );
}
