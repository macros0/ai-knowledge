"use client";

import { useTheme } from "@/context/ThemeContext";
import { useI18n } from "@/i18n/LocaleContext";
import { AutoThemeIcon, MoonIcon, SunIcon } from "@/components/icons";

// Порядок цикла: auto → dark → light → auto. Первый клик из «Авто» ведёт на
// Тёмную (дефолт и no-JS fallback) — без визуального скачка для большинства.
const CYCLE = ["auto", "dark", "light"];

const ICONS = {
  auto: AutoThemeIcon,
  dark: MoonIcon,
  light: SunIcon,
};

export default function ThemeToggle() {
  const { mode, setMode } = useTheme();
  const { t } = useI18n();
  const Icon = ICONS[mode] ?? AutoThemeIcon;
  const next = CYCLE[(CYCLE.indexOf(mode) + 1) % CYCLE.length];

  const label = {
    auto: t("theme.auto"),
    dark: t("theme.dark"),
    light: t("theme.light"),
  }[mode] ?? t("theme.auto");
  const nextLabel =
    { auto: t("theme.auto"), dark: t("theme.dark"), light: t("theme.light") }[next] ?? t("theme.auto");

  return (
    <button
      type="button"
      className="icon-btn theme-toggle-btn"
      onClick={() => setMode(next)}
      aria-label={t("theme.ariaLabel", { label })}
      title={t("theme.title", { label, nextLabel })}
    >
      <Icon size={16} />
    </button>
  );
}