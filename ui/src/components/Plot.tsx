import { useEffect, useRef, useState } from "react";
import type {
  Data,
  Layout,
  PlotMouseEvent,
  PlotlyHTMLElement,
} from "plotly.js";
export interface PlotProps {
  threeD?: boolean;
  data: Data[];
  title: string;
  layout?: Partial<Layout>;
  onSelect?: (event: PlotMouseEvent) => void;
  height?: number;
}
const EMPTY_LAYOUT: Partial<Layout> = {};
const loadPlotly = (threeD: boolean) =>
  threeD
    ? import("plotly.js-gl3d-dist-min")
    : import("plotly.js-cartesian-dist-min");
export default function Plot({
  data,
  title,
  layout = EMPTY_LAYOUT,
  onSelect,
  height = 420,
  threeD = false,
}: PlotProps) {
  const ref = useRef<HTMLDivElement>(null),
    select = useRef(onSelect);
  const [error, setError] = useState("");
  select.current = onSelect;
  useEffect(() => {
    let active = true,
      observer: ResizeObserver | undefined;
    const element = ref.current!;
    void loadPlotly(threeD)
      .then(async ({ default: Plotly }) => {
        if (!active) return;
        await Plotly.react(
          element,
          data,
          {
            autosize: true,
            paper_bgcolor: "transparent",
            plot_bgcolor: "transparent",
            margin: { l: 58, r: 24, t: 30, b: 70 },
            font: {
              family: "Inter, Segoe UI, sans-serif",
              color: "#4b6571",
              size: 12,
            },
            hoverlabel: {
              bgcolor: "#142b38",
              font: { color: "#fff", size: 12 },
            },
            legend: { orientation: "h", y: -0.21, x: 0, font: { size: 11 } },
            ...layout,
          },
          {
            responsive: false,
            displaylogo: false,
            modeBarButtonsToRemove: ["select2d", "lasso2d"],
            toImageButtonOptions: {
              format: "svg",
              filename: "qcross-portfolio-lab",
            },
          },
        );
        if (!active) return;
        const graph = element as unknown as PlotlyHTMLElement;
        graph.removeAllListeners?.("plotly_click");
        graph.on("plotly_click", (event: PlotMouseEvent) =>
          select.current?.(event),
        );
        observer = new ResizeObserver(() => {
          if (
            active &&
            element.isConnected &&
            element.clientWidth &&
            element.clientHeight
          )
            void Promise.resolve(Plotly.Plots.resize(element)).catch(
              () => undefined,
            );
        });
        observer.observe(element);
      })
      .catch(() => {
        if (active)
          setError(
            "Не удалось загрузить график Plotly. Перезагрузите страницу.",
          );
      });
    return () => {
      active = false;
      observer?.disconnect();
    };
  }, [data, layout, threeD]);
  useEffect(() => {
    const element = ref.current;
    return () => {
      void loadPlotly(threeD).then(({ default: Plotly }) => {
        if (element && !element.isConnected) Plotly.purge(element);
      });
    };
  }, [threeD]);
  return (
    <div className="plot-wrap" role="img" aria-label={title}>
      {error ? (
        <p role="alert">{error}</p>
      ) : (
        <div ref={ref} style={{ width: "100%", height }} />
      )}
    </div>
  );
}
