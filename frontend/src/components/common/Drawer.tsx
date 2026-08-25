import { useEffect } from "react";
import { X } from "lucide-react";
import clsx from "clsx";

interface Props {
  open: boolean;
  onClose: () => void;
  title: string;
  icon?: React.ReactNode;
  /** "tall" => 78vh, "half" => 55vh */
  height?: "tall" | "half";
  children: React.ReactNode;
}

export default function Drawer({
  open,
  onClose,
  title,
  icon,
  height = "tall",
  children,
}: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        className={clsx(
          "fixed inset-0 z-40 transition-opacity duration-300",
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        )}
        style={{ background: "var(--backdrop)", backdropFilter: "blur(4px)" }}
        onClick={onClose}
      />

      {/* Sheet */}
      <div
        className={clsx(
          "fixed left-1/2 -translate-x-1/2 bottom-0 z-50 w-full max-w-5xl",
          "rounded-t-2xl flex flex-col transition-transform duration-300",
          height === "tall" ? "h-[78vh]" : "h-[55vh]",
          open ? "translate-y-0" : "translate-y-full"
        )}
        style={{
          background: "var(--drawer-bg)",
          borderTop: "1px solid var(--border-accent)",
          borderLeft: "1px solid var(--border-dim)",
          borderRight: "1px solid var(--border-dim)",
          backdropFilter: "blur(24px)",
          boxShadow: "var(--shadow-lg)",
        }}
      >
        {/* Drag handle */}
        <div className="pt-2.5 pb-1 flex justify-center shrink-0">
          <div
            className="w-8 h-0.5 rounded-full"
            style={{ background: "rgba(255,255,255,0.12)" }}
          />
        </div>

        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-2.5 shrink-0"
          style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}
        >
          <div className="flex items-center gap-2 text-sm font-medium" style={{ color: "#e2e8f0" }}>
            {icon}
            {title}
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md transition-colors"
            style={{ color: "rgba(100,116,139,0.7)" }}
            title="Close (Esc)"
          >
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 min-h-0 overflow-hidden">{children}</div>
      </div>
    </>
  );
}
