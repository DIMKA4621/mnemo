"use client";

import { Modal } from "antd";
import { useT } from "@/lib/i18n/hooks";

interface ModalShellProps {
  open: boolean;
  title: string;
  ariaLabel?: string;
  onClose: () => void;
  /** Disables Esc/backdrop/✕ dismissal while a destructive action is mid-flight. */
  busy?: boolean;
  wide?: boolean;
  footer?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * Shared chrome for every Памʼять dialog (bank picker, token panel, removal
 * confirmation, rebuild-pending). A thin wrapper over AntD's `Modal` rather
 * than a hand-rolled overlay — Esc/backdrop/close-button dismissal all come
 * from AntD for free; this only pins the close-button title, the
 * dismiss-while-busy guard and the width convention every dialog here shares.
 */
export function ModalShell({ open, title, ariaLabel, onClose, busy, wide, footer, children }: ModalShellProps) {
  const t = useT();
  return (
    <Modal
      open={open}
      onCancel={busy ? undefined : onClose}
      closable={!busy}
      mask={{ closable: !busy }}
      keyboard={!busy}
      title={title}
      aria-label={ariaLabel ?? title}
      footer={footer ?? null}
      width={wide ? 640 : undefined}
      closeIcon={<span title={t("common.btn.closeEsc")}>✕</span>}
      destroyOnHidden
    >
      {children}
    </Modal>
  );
}
