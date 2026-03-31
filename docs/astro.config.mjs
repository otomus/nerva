import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

export default defineConfig({
  integrations: [
    starlight({
      title: "Nerva",
      description: "Composable Agent Primitives",
      social: {
        github: "https://github.com/otomus/nerva",
      },
      sidebar: [
        {
          label: "Getting Started",
          items: [
            { label: "Python", slug: "getting-started/python" },
            { label: "TypeScript", slug: "getting-started/typescript" },
          ],
        },
        {
          label: "Primitives",
          items: [
            { label: "Overview", slug: "primitives/overview" },
            { label: "ExecContext", slug: "primitives/context" },
            { label: "Router", slug: "primitives/router" },
            { label: "Runtime", slug: "primitives/runtime" },
            { label: "Tools", slug: "primitives/tools" },
            { label: "Memory", slug: "primitives/memory" },
            { label: "Responder", slug: "primitives/responder" },
            { label: "Registry", slug: "primitives/registry" },
            { label: "Policy", slug: "primitives/policy" },
          ],
        },
        {
          label: "Architecture",
          items: [
            { label: "Overview", slug: "architecture" },
          ],
        },
        {
          label: "Guides",
          items: [
            { label: "Migration Guide", slug: "guides/migration" },
            { label: "Cookbook", slug: "guides/cookbook" },
          ],
        },
      ],
    }),
  ],
});
