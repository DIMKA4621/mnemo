"use client";

import { useEffect } from "react";
import { ConfigProvider } from "antd";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { QueryClientProvider } from "@tanstack/react-query";
import { NextIntlClientProvider } from "next-intl";
import { queryClient } from "@/lib/query/client";
import { buildAntdTheme } from "@/lib/theme/antd-theme";
import { messagesFor } from "@/lib/i18n/messages";
import { useUiStore } from "@/lib/store/ui";
import { useTokenStore } from "@/lib/store/token";
import { initWsClient } from "@/lib/ws/client";

const antdTheme = buildAntdTheme();

export function Providers({ children }: { children: React.ReactNode }) {
  const lang = useUiStore((s) => s.lang);
  const hydrateUi = useUiStore((s) => s.hydrate);
  const hydrateToken = useTokenStore((s) => s.hydrate);

  useEffect(() => {
    // Order matters: the token must be resolved (from `?token=` or
    // `sessionStorage`) before the WS client's first `connectSocket()`
    // call reads it, and `hydrateUi()` before render avoids painting the
    // wrong theme/language for longer than the inline bootstrap script
    // already unavoidably does for the very first frame.
    hydrateUi();
    hydrateToken();
    const teardown = initWsClient();
    return teardown;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <AntdRegistry>
      <ConfigProvider theme={antdTheme}>
        <QueryClientProvider client={queryClient}>
          <NextIntlClientProvider locale={lang} messages={messagesFor(lang)} timeZone="UTC">
            {children}
          </NextIntlClientProvider>
        </QueryClientProvider>
      </ConfigProvider>
    </AntdRegistry>
  );
}
