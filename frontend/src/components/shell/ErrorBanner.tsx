"use client";

import { Alert } from "antd";
import { useErrorBannerStore } from "@/lib/store/error-banner";

export function ErrorBanner() {
  const message = useErrorBannerStore((s) => s.message);
  const hide = useErrorBannerStore((s) => s.hide);
  if (!message) return null;

  return (
    <Alert
      type="error"
      message={message}
      closable
      onClose={hide}
      style={{ borderRadius: 0, borderLeft: "none", borderRight: "none" }}
    />
  );
}
