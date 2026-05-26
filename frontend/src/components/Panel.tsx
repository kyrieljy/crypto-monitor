import type { ReactNode } from "react";
import { GripVertical } from "lucide-react";

interface PanelProps {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  dragHandle?: boolean;
}

export function Panel({ title, action, children, dragHandle = true }: PanelProps) {
  return (
    <section className="panel">
      <div className="panel__head">
        <div className="panel__title">
          {dragHandle && <GripVertical className="drag-handle" size={16} />}
          <span>{title}</span>
        </div>
        {action}
      </div>
      <div className="panel__body">{children}</div>
    </section>
  );
}
