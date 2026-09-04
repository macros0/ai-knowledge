"use client";

import { useTheme } from "@/context/ThemeContext";
import { AutoThemeIcon, MoonIcon, SunIcon } from "@/components/icons";

const MODE_LABEL = {
  auto: "Авто",
  dark: "Тёмная",
  light: "Светлая",
};

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
  const Icon = ICONS[mode] ?? AutoThemeIcon;
  const next = CYCLE[(CYCLE.indexOf(mode) + 1) % CYCLE.length];

  return (
    <button
      type="button"
      className="icon-btn theme-toggle-btn"
      onClick={() => setMode(next)}
      aria-label={`Тема оформления: ${MODE_LABEL[mode]}`}
      title={`Тема: ${MODE_LABEL[mode]} · клик — переключить на ${MODE_LABEL[next]}`}
    >
      <Icon size={16} />
    </button>
  );
}
