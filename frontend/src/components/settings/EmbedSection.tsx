"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Segmented, Select, Input, InputNumber } from "antd";
import { useT, useTMaybe } from "@/lib/i18n/hooks";
import { InlineNote, useInlineNote } from "@/components/common/InlineNote";
import { useSettings } from "@/hooks/useSettingsQueries";
import { usePutSettings } from "@/hooks/useSettingsMutations";
import { getBanks } from "@/lib/api/memory";
import { queryKeys } from "@/lib/query/keys";
import { ApiError } from "@/lib/api/fetcher";
import type { BackendPreset, ModelPreset, SettingsPutPayload, SettingsResult } from "@/lib/api/settings";
import { EmbedMemoryCard } from "./EmbedMemoryCard";
import type { SectionController } from "./SettingsTabs";

interface EmbedForm {
  model: string;
  url: string;
  dim: number;
  timeout: number;
  key: string;
}

/** Which backend the stored settings correspond to — `provider` alone does
 *  not answer it (`ollama` and `openai` are both the `api` provider); the
 *  URL is what tells them apart. Ported from the vanilla console's
 *  `backendForSettings()`. */
function backendForSettings(settings: SettingsResult): string {
  const provider = settings.settings.provider?.value ?? "local";
  if (provider === "local") return "local";
  const url = (settings.settings["api.url"]?.value ?? "").trim();
  const match = settings.presets.find((b) => b.provider === "api" && b.url && b.url === url);
  if (match) return match.id;
  const anyApi = settings.presets.find((b) => b.provider === "api");
  return anyApi ? anyApi.id : "local";
}

function seedForm(settings: SettingsResult, backendId: string, backend: BackendPreset | null): EmbedForm {
  const stored = {
    url: settings.settings["api.url"]?.value ?? "",
    model: settings.settings["api.model"]?.value ?? "",
    dim: settings.settings["api.dim"]?.value ?? 0,
    timeout: settings.settings["api.timeout"]?.value ?? 60,
  };
  // Carried across only when this tab IS the stored backend — switching to
  // OpenAI must not inherit Ollama's URL, which would look deliberate and
  // cannot work.
  const isStored = backendId === backendForSettings(settings);
  const known = backend?.models[0] ?? null;
  const model = (isStored && stored.model) || (known ? known.name : "");
  const preset = backend?.models.find((m) => m.name === model) ?? null;
  return {
    model,
    url: (isStored && stored.url) || (backend ? backend.url : ""),
    dim: (isStored && stored.dim) || (preset ? preset.dim : 0),
    timeout: (isStored && stored.timeout) || 60,
    key: "",
  };
}

function pendingRebuildCount(banks: { rebuild_pending: boolean }[]): number {
  return banks.filter((b) => b.rebuild_pending).length;
}

interface EmbedSectionProps {
  onController: (controller: SectionController | null) => void;
}

/** Backend, model, endpoint — the settings that decide what produces
 *  vectors. The catalogue (`GET /api/settings`'s `presets`) is what makes
 *  the backend **picked, not typed**: e5 needs mandatory `passage: `/
 *  `query: ` markers, and a free-text form would make forgetting them
 *  possible; choosing a model here gets URL, width and prefixes all at once. */
export function EmbedSection({ onController }: EmbedSectionProps) {
  const t = useT();
  const tMaybe = useTMaybe();
  const qc = useQueryClient();
  const settingsQuery = useSettings();
  const putSettingsMutation = usePutSettings();

  const [backendId, setBackendId] = useState<string | null>(null);
  const [form, setForm] = useState<EmbedForm | null>(null);
  const [keyTouched, setKeyTouched] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [note, setNote] = useInlineNote();
  const seededRef = useRef(false);

  useEffect(() => {
    if (seededRef.current || !settingsQuery.data) return;
    seededRef.current = true;
    const id = backendForSettings(settingsQuery.data);
    const backend = settingsQuery.data.presets.find((b) => b.id === id) ?? null;
    setBackendId(id);
    setForm(seedForm(settingsQuery.data, id, backend));
  }, [settingsQuery.data]);

  function chooseBackend(id: string) {
    if (!settingsQuery.data || id === backendId) return;
    const backend = settingsQuery.data.presets.find((b) => b.id === id) ?? null;
    setBackendId(id);
    setForm(seedForm(settingsQuery.data, id, backend));
    setErrorText(null);
    setNote(null);
  }

  function chooseModel(name: string) {
    if (!settingsQuery.data || !backendId || !form) return;
    const backend = settingsQuery.data.presets.find((b) => b.id === backendId) ?? null;
    const preset = backend?.models.find((m) => m.name === name) ?? null;
    setForm({ ...form, model: name, dim: preset ? preset.dim : form.dim });
    setErrorText(null);
    setNote(null);
  }

  const stored = settingsQuery.data ? backendForSettings(settingsQuery.data) : null;
  const hasPendingChange = !!settingsQuery.data && backendId !== stored;

  async function submit() {
    if (!settingsQuery.data || !backendId || !form) return;
    const backend = settingsQuery.data.presets.find((b) => b.id === backendId) ?? null;
    if (!backend) return;
    setErrorText(null);
    setNote(null);

    const payload: SettingsPutPayload = { provider: backend.provider };
    if (backend.provider === "api") {
      if (!form.url.trim()) {
        setErrorText(t("settings.embed.errors.missingUrl"));
        return;
      }
      if (!(form.dim > 0)) {
        setErrorText(t("settings.embed.errors.dimNotPositive"));
        return;
      }
      payload.api = { url: form.url.trim(), model: form.model, dim: form.dim, timeout: form.timeout || 60 };
      // Sent only when typed — an untouched field means "leave what is
      // stored"; sending its empty value would erase a working credential.
      if (keyTouched) payload.api.key = form.key;
    }

    try {
      await putSettingsMutation.mutateAsync(payload);
      setKeyTouched(false);
      setForm({ ...form, key: "" });
      // The provider cache the PUT dropped is re-read here too: the memory
      // block must point at the saved endpoint, and the note below must
      // report the resulting stale indexes immediately.
      const [banksResult] = await Promise.allSettled([
        qc.fetchQuery({ queryKey: queryKeys.banks.all, queryFn: async () => (await getBanks()).banks }),
        qc.invalidateQueries({ queryKey: queryKeys.embedState.all }),
        qc.invalidateQueries({ queryKey: queryKeys.status.all }),
      ]);
      const refreshFailed = banksResult.status === "rejected";
      const pendingCount = banksResult.status === "fulfilled" ? pendingRebuildCount(banksResult.value) : 0;
      setNote(
        settingsQuery.data?.restart_required
          ? t("settings.embed.saved.restartRequired")
          : t(pendingCount ? "settings.embed.saved.appliedPending" : "settings.embed.saved.appliedNoPending"),
      );
      if (refreshFailed) setErrorText(t("settings.embed.errors.refreshFailed"));
    } catch (err) {
      setErrorText(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    onController({ hasPendingChange, busy: putSettingsMutation.isPending, submit });
    return () => onController(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingChange, putSettingsMutation.isPending, backendId, form, keyTouched]);

  if (!settingsQuery.data || !backendId || !form) {
    return <p className="empty-hint">{t("settings.loading")}</p>;
  }

  const backend = settingsQuery.data.presets.find((b) => b.id === backendId) ?? null;
  const providerItem = settingsQuery.data.settings.provider;

  function backendLabel(item: BackendPreset): string {
    return tMaybe(`settings.embed.backend.${item.id}.label`) ?? item.label;
  }
  function backendNoteText(item: BackendPreset | null): string | null {
    if (!item) return null;
    return tMaybe(`settings.embed.backend.${item.id}.note`) ?? item.note ?? null;
  }
  function modelNoteText(model: ModelPreset | null): string | null {
    if (!model?.note) return model?.note ?? null;
    return tMaybe(`settings.embed.model.${model.name}.note`) ?? model.note;
  }

  const chosenModel = backend?.models.find((m) => m.name === form.model) ?? null;

  return (
    <>
      <p className="set-warn">
        {t("settings.embed.warn.line1")}
        <br />
        {t("settings.embed.warn.line2")}
        <br />
        {t("settings.embed.warn.line3")}
      </p>

      <div className="set-field">
        <span className="set-label">{t("settings.embed.backendLabel")}</span>
        <Segmented
          className="set-backend-tabs"
          value={backendId}
          onChange={(v) => chooseBackend(v as string)}
          options={settingsQuery.data.presets.map((item) => ({ label: backendLabel(item), value: item.id }))}
        />
        {backendNoteText(backend) && <p className="set-note">{backendNoteText(backend)}</p>}
      </div>

      {stored != null && backendId !== stored && (
        <p className="set-override">
          {t("settings.embed.notSavedBackend", {
            active: (() => {
              const activeBackend = settingsQuery.data!.presets.find((b) => b.id === stored) ?? null;
              return activeBackend ? backendLabel(activeBackend) : "—";
            })(),
            target: backend ? backendLabel(backend) : "—",
          })}
        </p>
      )}
      {providerItem?.overridden && <p className="set-override">{t("settings.overrideNote", { var: providerItem.env_var })}</p>}

      {!backend ? (
        <InlineNote text={note} tone="success" />
      ) : backend.provider === "local" ? (
        <>
          <p className="set-lead">
            {t("settings.embed.local.lead")}
            <code>{backend.models[0]?.label ?? "—"}</code>
            {backend.models[0] ? t("settings.embed.local.dimsSuffix", { dim: backend.models[0].dim }) : t("settings.embed.local.noDimSuffix")}
            {t("settings.embed.local.tail")}
          </p>
          {backendId === stored && (
            <>
              <div className="set-divider" />
              <EmbedMemoryCard />
            </>
          )}
          {errorText ? <p className="modal-error">{errorText}</p> : <InlineNote text={note} tone="success" />}
        </>
      ) : (
        <>
          <div className="set-field">
            <span className="set-label">{t("settings.embed.modelLabel")}</span>
            <Select
              className="set-wide"
              value={form.model}
              onChange={chooseModel}
              options={[
                ...backend.models.map((m) => ({ label: m.label, value: m.name })),
                ...(form.model && !backend.models.some((m) => m.name === form.model)
                  ? [{ label: form.model + t("settings.embed.model.notInCatalog"), value: form.model }]
                  : []),
              ]}
            />
            {modelNoteText(chosenModel) && <p className="set-note">{modelNoteText(chosenModel)}</p>}
          </div>
          {chosenModel?.prefixed && <p className="set-note">{t("settings.embed.model.prefixedNote")}</p>}
          {settingsQuery.data.settings["api.model"]?.overridden && (
            <p className="set-override">{t("settings.overrideNote", { var: settingsQuery.data.settings["api.model"]!.env_var })}</p>
          )}

          <div className="set-field">
            <span className="set-label">{t("settings.embed.urlLabel")}</span>
            <Input
              className="set-wide"
              spellCheck={false}
              placeholder="http://…"
              value={form.url}
              onChange={(e) => {
                setForm({ ...form, url: e.target.value });
                setErrorText(null);
                setNote(null);
              }}
            />
          </div>
          {settingsQuery.data.settings["api.url"]?.overridden && (
            <p className="set-override">{t("settings.overrideNote", { var: settingsQuery.data.settings["api.url"]!.env_var })}</p>
          )}

          <div className="set-divider" />
          <div className="set-row">
            <div className="set-field">
              <span className="set-label">{t("settings.embed.dimLabel")}</span>
              <InputNumber
                className="set-narrow"
                min={1}
                step={1}
                value={form.dim || undefined}
                onChange={(v) => {
                  setForm({ ...form, dim: Number(v) || 0 });
                  setErrorText(null);
                  setNote(null);
                }}
              />
            </div>
            <div className="set-field">
              <span className="set-label">{t("settings.embed.timeoutLabel")}</span>
              <InputNumber
                className="set-narrow"
                min={1}
                step={1}
                value={form.timeout || undefined}
                onChange={(v) => {
                  setForm({ ...form, timeout: Number(v) || 0 });
                  setErrorText(null);
                  setNote(null);
                }}
              />
            </div>
          </div>
          <p className="set-note">{t("settings.embed.dimNote")}</p>
          {settingsQuery.data.settings["api.dim"]?.overridden && (
            <p className="set-override">{t("settings.overrideNote", { var: settingsQuery.data.settings["api.dim"]!.env_var })}</p>
          )}
          {settingsQuery.data.settings["api.timeout"]?.overridden && (
            <p className="set-override">{t("settings.overrideNote", { var: settingsQuery.data.settings["api.timeout"]!.env_var })}</p>
          )}

          {backend.needs_key && (
            <>
              <div className="set-divider" />
              <div className="set-field">
                <span className="set-label">{t("settings.embed.keyLabel")}</span>
                <Input.Password
                  className="set-wide"
                  autoComplete="off"
                  placeholder={settingsQuery.data.settings["api.key_set"]?.value ? t("settings.embed.key.placeholderStored") : "sk-…"}
                  value={form.key}
                  onChange={(e) => {
                    setForm({ ...form, key: e.target.value });
                    setKeyTouched(true);
                  }}
                />
                <p className="set-note">{t("settings.embed.keyNote")}</p>
              </div>
              {settingsQuery.data.settings["api.key_set"]?.overridden && (
                <p className="set-override">{t("settings.overrideNote", { var: settingsQuery.data.settings["api.key_set"]!.env_var })}</p>
              )}
            </>
          )}

          {backendId === stored && (
            <>
              <div className="set-divider" />
              <EmbedMemoryCard />
            </>
          )}

          {errorText ? <p className="modal-error">{errorText}</p> : <InlineNote text={note} tone="success" />}
        </>
      )}
    </>
  );
}
