"use client";

import { useEffect, useLayoutEffect } from "react";
import { ConfigProvider } from "antd";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { QueryClientProvider } from "@tanstack/react-query";
import { NextIntlClientProvider } from "next-intl";
import { queryClient } from "@/lib/query/client";
import { antdThemes } from "@/lib/theme/antd-theme";
import { messagesFor } from "@/lib/i18n/messages";
import { useUiStore } from "@/lib/store/ui";
import { useTokenStore } from "@/lib/store/token";
import { initWsClient } from "@/lib/ws/client";

export function Providers({ children }: { children: React.ReactNode }) {
  const lang = useUiStore((s) => s.lang);
  const themeMode = useUiStore((s) => s.theme);
  const hydrateUi = useUiStore((s) => s.hydrate);
  const hydrateToken = useTokenStore((s) => s.hydrate);

  // A layout effect, not a passive one: its state update re-renders before
  // the browser paints, so AntD never shows a frame in the default (dark)
  // theme or language when the stored preference differs. The same paint
  // reveals the main area the bootstrap script in `layout.tsx` hid.
  useLayoutEffect(() => {
    hydrateUi();
    delete document.documentElement.dataset.hydrating;
  }, [hydrateUi]);

  useEffect(() => {
    // The token must be resolved (from `?token=` or `sessionStorage`)
    // before the WS client's first `connectSocket()` call reads it.
    hydrateToken();
    const teardown = initWsClient();
    return teardown;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AntdRegistry>
      <ConfigProvider theme={antdThemes[themeMode]}>
        <QueryClientProvider client={queryClient}>
          <NextIntlClientProvider locale={lang} messages={messagesFor(lang)} timeZone="UTC">
            {children}
          </NextIntlClientProvider>
        </QueryClientProvider>
      </ConfigProvider>
    </AntdRegistry>
  );
}
