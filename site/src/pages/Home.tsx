import { motion } from "framer-motion";
import {
  ArrowRight, Github, Minus, Box, Layers,
  Radio, ShieldCheck, Plug, Activity,
  Check, X as XIcon, FlaskConical,
} from "lucide-react";

const FADE_UP = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
};

const ORB_FLOAT = {
  y: [0, -30, 0],
  x: [0, 15, -15, 0],
  scale: [1, 1.05, 0.95, 1],
};

export function HomePage() {
  return (
    <>
      <Hero />
      <Section id="problem" bg="alt"><Problem /></Section>
      <Section id="ecosystem" bg="default"><Ecosystem /></Section>
      <Section id="testkit" bg="alt"><Testkit /></Section>
      <Section id="comparison" bg="default"><Comparison /></Section>
      <Section id="stats" bg="alt"><Stats /></Section>
      <Section id="cta" bg="default"><CTA /></Section>
    </>
  );
}

/** Wrapper that gives each section a distinct background and top border. */
function Section({ children, id, bg }: { children: React.ReactNode; id: string; bg: "default" | "alt" }) {
  const bgClass = bg === "alt" ? "bg-[#060d1f]" : "bg-[#030710]";
  return (
    <div id={id} className={`${bgClass} border-t border-white/[0.04]`}>
      {children}
    </div>
  );
}

/* ─── Hero ─────────────────────────────────────────────────────────── */

function Hero() {
  return (
    <section className="relative min-h-[85vh] flex items-center justify-center overflow-hidden">
      <motion.div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full bg-gradient-to-br from-[#7FC8FF]/20 via-[#7FC8FF]/5 to-transparent blur-3xl pointer-events-none"
        animate={ORB_FLOAT}
        transition={{ duration: 8, repeat: Infinity, repeatType: "mirror", ease: "easeInOut" }}
        aria-hidden
      />

      <motion.div
        className="relative z-10 mx-auto max-w-4xl px-6 text-center"
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: 0.15 } } }}
      >
        <motion.div variants={FADE_UP} transition={{ duration: 0.5 }}>
          <span className="inline-block px-4 py-1.5 rounded-full border border-[#7FC8FF]/20 text-[#7FC8FF] text-sm mb-8">
            Python &middot; TypeScript &middot; Go &middot; Rust
          </span>
        </motion.div>

        <motion.h1
          variants={FADE_UP}
          transition={{ duration: 0.6 }}
          className="text-glow text-5xl md:text-7xl font-bold tracking-tight leading-tight"
        >
          Composable <span className="text-[#7FC8FF]">Agent</span>
          <br />
          Primitives
        </motion.h1>

        <motion.p
          variants={FADE_UP}
          transition={{ duration: 0.6 }}
          className="mt-6 text-lg md:text-xl text-gray-400 max-w-2xl mx-auto leading-relaxed"
        >
          Build your agent system — not the plumbing around it.
          <br className="hidden sm:block" />
          8 primitives. Use one, use all, replace any.
        </motion.p>

        <motion.div
          variants={FADE_UP}
          transition={{ duration: 0.6 }}
          className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-4"
        >
          <a
            href="/nerva/docs/"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-[#7FC8FF] text-[#030710] font-semibold hover:bg-[#7FC8FF]/90 transition-colors"
          >
            Get Started <ArrowRight size={18} />
          </a>
          <a
            href="https://github.com/otomus/nerva"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-lg border border-white/20 text-white hover:border-[#7FC8FF]/50 transition-colors"
          >
            <Github size={18} /> View on GitHub
          </a>
        </motion.div>

        <motion.div
          variants={FADE_UP}
          transition={{ duration: 0.6 }}
          className="mt-16"
        >
          <CodeSnippet />
        </motion.div>
      </motion.div>
    </section>
  );
}

function CodeSnippet() {
  return (
    <div className="code-block mx-auto max-w-xl text-left p-6 text-sm">
      <div className="flex gap-1.5 mb-4">
        <div className="w-3 h-3 rounded-full bg-red-500/60" />
        <div className="w-3 h-3 rounded-full bg-yellow-500/60" />
        <div className="w-3 h-3 rounded-full bg-green-500/60" />
      </div>
      <pre className="text-gray-300 overflow-x-auto">
        <code>
          <span className="text-[#7FC8FF]">from</span> nerva <span className="text-[#7FC8FF]">import</span> Orchestrator{"\n"}
          <span className="text-[#7FC8FF]">from</span> nerva.router <span className="text-[#7FC8FF]">import</span> HybridRouter{"\n"}
          <span className="text-[#7FC8FF]">from</span> nerva.tools <span className="text-[#7FC8FF]">import</span> MCPToolManager{"\n"}
          {"\n"}
          <span className="text-gray-500"># Compose only what you need</span>{"\n"}
          agent = Orchestrator({"\n"}
          {"    "}router=HybridRouter(agents),{"\n"}
          {"    "}tools=MCPToolManager(),{"\n"}
          {"    "}policy=YamlPolicyEngine(<span className="text-emerald-400">"nerva.yaml"</span>),{"\n"}
          )
        </code>
      </pre>
    </div>
  );
}

/* ─── Problem ──────────────────────────────────────────────────────── */

function Problem() {
  return (
    <section className="py-28 px-6">
      <div className="mx-auto max-w-6xl">
        <SectionHeading
          title="The Gap in Agent Tooling"
          subtitle="SDKs are too low-level — you rebuild routing, execution, tools, memory every time. Frameworks are too opinionated — they take over your architecture. There is no middle ground."
        />

        <motion.div
          className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-16"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={{ visible: { transition: { staggerChildren: 0.1 } } }}
        >
          <motion.div variants={FADE_UP} transition={{ duration: 0.5 }} className="glass-card rounded-xl p-8">
            <Minus size={28} className="text-gray-500 mb-4" />
            <h3 className="text-lg font-semibold text-white mb-2">SDKs</h3>
            <p className="text-gray-400 text-sm leading-relaxed">
              Too low-level. You rebuild routing, execution, tools, and memory every single time.
            </p>
          </motion.div>

          <motion.div
            variants={FADE_UP}
            transition={{ duration: 0.5 }}
            className="glass-card rounded-xl p-8 border-[#7FC8FF]/30 bg-[#7FC8FF]/5"
          >
            <Layers size={28} className="text-[#7FC8FF] mb-4" />
            <h3 className="text-lg font-semibold text-[#7FC8FF] mb-2">Nerva</h3>
            <p className="text-gray-300 text-sm leading-relaxed">
              The infrastructure every agent system needs — without owning your system. Library, not framework.
            </p>
          </motion.div>

          <motion.div variants={FADE_UP} transition={{ duration: 0.5 }} className="glass-card rounded-xl p-8">
            <Box size={28} className="text-gray-500 mb-4" />
            <h3 className="text-lg font-semibold text-white mb-2">Frameworks</h3>
            <p className="text-gray-400 text-sm leading-relaxed">
              Too opinionated. They own your architecture. Swap a component and fight the framework.
            </p>
          </motion.div>
        </motion.div>
      </div>
    </section>
  );
}

/* ─── Ecosystem ────────────────────────────────────────────────────── */

const INFRA = [
  {
    icon: Radio,
    name: "NATS JetStream",
    desc: "Distributed transport layer. Pub/sub messaging, key-value store, persistent streams. Production-grade at any scale.",
  },
  {
    icon: ShieldCheck,
    name: "mcp-armor",
    desc: "Tool sandboxing and security. Every MCP tool call runs through armor policies — no unaudited access to your systems.",
  },
  {
    icon: Plug,
    name: "MCP Protocol",
    desc: "Model Context Protocol for universal tool discovery. Connect any MCP-compatible server — filesystem, databases, APIs.",
  },
  {
    icon: Activity,
    name: "OpenTelemetry",
    desc: "Native OTLP export. Traces, spans, and cost tracking flow to Jaeger, Datadog, Honeycomb — zero custom wiring.",
  },
];

function Ecosystem() {
  return (
    <section className="py-28 px-6">
      <div className="mx-auto max-w-6xl">
        <SectionHeading
          title="Battle-Tested Infrastructure"
          subtitle="Nerva doesn't reinvent the wheel. It integrates with the infrastructure you already trust."
        />

        <motion.div
          className="grid grid-cols-1 sm:grid-cols-2 gap-6 mt-16"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={{ visible: { transition: { staggerChildren: 0.1 } } }}
        >
          {INFRA.map(({ icon: Icon, name, desc }) => (
            <motion.div
              key={name}
              variants={FADE_UP}
              transition={{ duration: 0.5 }}
              className="glass-card rounded-xl p-8"
            >
              <Icon size={32} className="text-[#7FC8FF] mb-4" />
              <h3 className="text-lg font-semibold text-white mb-2">{name}</h3>
              <p className="text-sm text-gray-400 leading-relaxed">{desc}</p>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}

/* ─── Testkit ──────────────────────────────────────────────────────── */

function Testkit() {
  return (
    <section className="py-28 px-6">
      <div className="mx-auto max-w-6xl">
        <SectionHeading
          title="Testability Built In"
          subtitle="Every primitive has a spy wrapper. Real code runs — spies record every call. Set expectations at the boundary, assert against what happened. No mocks."
        />

        <motion.div
          className="grid grid-cols-1 lg:grid-cols-2 gap-8 mt-16"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={{ visible: { transition: { staggerChildren: 0.15 } } }}
        >
          <motion.div variants={FADE_UP} transition={{ duration: 0.5 }}>
            <div className="code-block rounded-xl p-6 text-sm h-full">
              <div className="flex items-center gap-2 mb-4">
                <FlaskConical size={16} className="text-[#7FC8FF]" />
                <span className="text-[#7FC8FF] text-xs font-semibold uppercase tracking-wider">Python</span>
              </div>
              <pre className="text-gray-300 overflow-x-auto">
                <code>
                  <span className="text-[#7FC8FF]">from</span> nerva.testkit <span className="text-[#7FC8FF]">import</span> ({"\n"}
                  {"    "}TestOrchestrator,{"\n"}
                  {"    "}assert_routed_to,{"\n"}
                  {"    "}assert_handler_invoked,{"\n"}
                  ){"\n"}
                  {"\n"}
                  <span className="text-gray-500"># One call — fully wired with spies</span>{"\n"}
                  orch = TestOrchestrator.build({"\n"}
                  {"    "}handlers={"{"}
                  <span className="text-emerald-400">"greet"</span>: my_handler{"}"},{"\n"}
                  ){"\n"}
                  {"\n"}
                  <span className="text-gray-500"># Set what the boundary returns</span>{"\n"}
                  orch.runtime.expect_result(<span className="text-emerald-400">"Hello!"</span>){"\n"}
                  {"\n"}
                  <span className="text-gray-500"># Real code runs. Assert against it.</span>{"\n"}
                  <span className="text-[#7FC8FF]">await</span> orch.orchestrator.handle(<span className="text-emerald-400">"hi"</span>, ctx){"\n"}
                  assert_routed_to(orch.router, <span className="text-emerald-400">"greet"</span>){"\n"}
                  assert_handler_invoked(orch.runtime, <span className="text-emerald-400">"greet"</span>)
                </code>
              </pre>
            </div>
          </motion.div>

          <motion.div variants={FADE_UP} transition={{ duration: 0.5 }} className="flex flex-col gap-6">
            {TESTKIT_FEATURES.map(({ title, desc }) => (
              <div key={title} className="glass-card rounded-xl p-6">
                <h3 className="text-white font-semibold mb-2">{title}</h3>
                <p className="text-sm text-gray-400 leading-relaxed">{desc}</p>
              </div>
            ))}
            <a
              href="/nerva/docs/guides/testing/"
              className="inline-flex items-center gap-2 text-[#7FC8FF] text-sm font-medium hover:underline"
            >
              Read the testing guide <ArrowRight size={14} />
            </a>
          </motion.div>
        </motion.div>
      </div>
    </section>
  );
}

const TESTKIT_FEATURES = [
  {
    title: "Spy wrappers, not mocks",
    desc: "SpyRouter, SpyRuntime, SpyMemory, SpyPolicy, SpyResponder, SpyToolManager — each delegates to the real implementation and records every call.",
  },
  {
    title: "Expectation queues",
    desc: "Set what a boundary returns with .expect_*(). Expectations are consumed FIFO, then fall back to real behavior. Mix real and canned responses in the same test.",
  },
  {
    title: "9 assertion helpers",
    desc: "assert_routed_to, assert_handler_invoked, assert_policy_denied, assert_tool_called, assert_pipeline_order — readable, purpose-built assertions.",
  },
  {
    title: "One-line setup",
    desc: "TestOrchestrator.build() gives you a fully wired system with spy-wrapped real defaults. Override any primitive — the rest stay wired.",
  },
];

/* ─── Comparison ───────────────────────────────────────────────────── */

interface ComparisonRow {
  readonly feature: string;
  readonly nerva: string;
  readonly langgraph: string;
  readonly crewai: string;
  readonly autogen: string;
  readonly pydantic: string;
}

const ROWS: ComparisonRow[] = [
  { feature: "Composable primitives", nerva: "8", langgraph: "No", crewai: "No", autogen: "No", pydantic: "No" },
  { feature: "You own the server", nerva: "Yes", langgraph: "No", crewai: "No", autogen: "No", pydantic: "Partial" },
  { feature: "Multi-language", nerva: "4", langgraph: "Python", crewai: "Python", autogen: "Python", pydantic: "Python" },
  { feature: "Tool sandboxing", nerva: "mcp-armor", langgraph: "None", crewai: "None", autogen: "None", pydantic: "None" },
  { feature: "Transport layer", nerva: "NATS", langgraph: "None", crewai: "None", autogen: "None", pydantic: "None" },
  { feature: "Memory tiers", nerva: "3-tier", langgraph: "Custom", crewai: "Short", autogen: "Chat", pydantic: "None" },
  { feature: "Policy engine", nerva: "YAML+code", langgraph: "Custom", crewai: "None", autogen: "None", pydantic: "None" },
  { feature: "Schema-driven", nerva: "TypeSpec", langgraph: "No", crewai: "No", autogen: "No", pydantic: "Pydantic" },
  { feature: "Built-in testkit", nerva: "Spies+Assert", langgraph: "No", crewai: "No", autogen: "No", pydantic: "No" },
];

const COMPETITORS = ["nerva", "langgraph", "crewai", "autogen", "pydantic"] as const;
const HEADERS = ["", "Nerva", "LangGraph", "CrewAI", "AutoGen", "Pydantic AI"];

function isPositive(val: string): boolean {
  return !["No", "None", "Python", "Custom", "Short", "Chat", "Partial"].includes(val);
}

function Comparison() {
  return (
    <section className="py-28 px-6">
      <div className="mx-auto max-w-6xl">
        <SectionHeading
          title="How Nerva Compares"
          subtitle="Side-by-side with the most popular agent frameworks."
        />

        <motion.div
          className="mt-16 overflow-x-auto"
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10">
                {HEADERS.map((h, i) => (
                  <th
                    key={h || "feature"}
                    className={`py-3 px-4 text-left font-semibold ${
                      i === 1 ? "text-[#7FC8FF]" : "text-white/60"
                    }`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => (
                <tr key={row.feature} className="border-b border-white/5">
                  <td className="py-3 px-4 text-white/80">{row.feature}</td>
                  {COMPETITORS.map((key) => {
                    const val = row[key];
                    const positive = isPositive(val);
                    return (
                      <td key={key} className="py-3 px-4">
                        <span className={`inline-flex items-center gap-1 ${
                          positive ? "text-[#7FC8FF]" : "text-white/30"
                        }`}>
                          {positive ? <Check size={14} /> : <XIcon size={14} />}
                          {val}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </motion.div>
      </div>
    </section>
  );
}

/* ─── Stats ────────────────────────────────────────────────────────── */

const STAT_ITEMS = [
  { value: "8", label: "Composable Primitives" },
  { value: "4", label: "Languages" },
  { value: "1,700+", label: "Tests" },
  { value: "35", label: "TypeSpec Schemas" },
];

function Stats() {
  return (
    <section className="py-28 px-6">
      <div className="mx-auto max-w-5xl">
        <motion.div
          className="grid grid-cols-2 md:grid-cols-4 gap-6"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={{ visible: { transition: { staggerChildren: 0.1 } } }}
        >
          {STAT_ITEMS.map(({ value, label }) => (
            <motion.div
              key={label}
              variants={FADE_UP}
              transition={{ duration: 0.5 }}
              className="glass-card rounded-xl p-6 text-center"
            >
              <div className="text-4xl md:text-5xl font-bold text-[#7FC8FF] text-glow-subtle mb-2">
                {value}
              </div>
              <div className="text-sm text-white/60">{label}</div>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}

/* ─── CTA ──────────────────────────────────────────────────────────── */

function CTA() {
  return (
    <section className="py-28 px-6">
      <motion.div
        className="mx-auto max-w-3xl text-center"
        initial={{ opacity: 0, y: 20 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.6 }}
      >
        <h2 className="text-glow text-4xl md:text-5xl font-bold mb-4">
          Build Your Agent System
        </h2>
        <p className="text-xl text-gray-400 mb-10">Not the plumbing around it.</p>
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
          <a
            href="/nerva/docs/"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-[#7FC8FF] text-[#030710] font-semibold hover:bg-[#7FC8FF]/90 transition-colors"
          >
            Get Started <ArrowRight size={18} />
          </a>
          <a
            href="https://github.com/otomus/nerva"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-lg border border-white/20 text-white hover:border-[#7FC8FF]/50 transition-colors"
          >
            <Github size={18} /> View on GitHub
          </a>
        </div>
      </motion.div>
    </section>
  );
}

/* ─── Shared ───────────────────────────────────────────────────────── */

function SectionHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <motion.div
      className="text-center max-w-2xl mx-auto"
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.6 }}
    >
      <h2 className="text-3xl md:text-4xl font-bold text-white mb-4">{title}</h2>
      <p className="text-gray-400 leading-relaxed">{subtitle}</p>
    </motion.div>
  );
}
