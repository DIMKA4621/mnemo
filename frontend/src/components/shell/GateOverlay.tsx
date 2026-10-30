"use client";

import { useState } from "react";
import { Button, Input, Modal, Typography } from "antd";
import { useTokenStore } from "@/lib/store/token";
import { useT } from "@/lib/i18n/hooks";

/**
 * Blocking overlay for a missing/rejected token (contract 9.1). Phase 1
 * keeps this functional rather than a pixel-match of the vanilla console's
 * `.gate` markup (`src/webui/static/app.js`'s `buildGate()`) — same latitude
 * the plan gives every shell component this phase: prove the mechanism
 * works against the real backend, refine the look later.
 */
export function GateOverlay() {
  const gateOpen = useTokenStore((s) => s.gateOpen);
  const gateReason = useTokenStore((s) => s.gateReason);
  const setToken = useTokenStore((s) => s.setToken);
  const t = useT();
  const [value, setValue] = useState("");

  const title = gateReason === "rejected" ? t("common.gate.rejected.title") : t("common.gate.missing.title");
  const text = gateReason === "rejected" ? t("common.gate.rejected.text") : t("common.gate.missing.text");

  function submit() {
    const trimmed = value.trim();
    if (!trimmed) return;
    setToken(trimmed);
    setValue("");
  }

  return (
    <Modal open={gateOpen} closable={false} footer={null} mask={{ closable: false }} centered title={title}>
      <Typography.Paragraph type="secondary">{text}</Typography.Paragraph>
      <Typography.Text code>mnemo ui</Typography.Text>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <Input
          placeholder={t("common.gate.tokenPlaceholder")}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onPressEnter={submit}
          autoFocus
        />
        <Button type="primary" onClick={submit}>
          {t("common.gate.submit")}
        </Button>
      </div>
    </Modal>
  );
}
