/**
 * Workspaces — what the lakehouse is, when to use it versus Genie One, and a
 * card per Databricks workspace the user's groups can use (from the Access
 * Rules), each opening that workspace or its Genie One.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ExternalLink, Loader2, Search, ShieldCheck, Users, X } from 'lucide-react';
import { Card, CardContent } from '../components/ui/card';
import { DatabricksIcon, GenieIcon } from '../components/icons/DatabricksIcons';
import { useBrandingStore } from '../stores/brandingStore';
import { listMyWorkspaces, type MyWorkspace, type MyWorkspaceList } from '../services/api';

const ALL_ENVIRONMENTS = 'all';
const MAX_GROUPS_SHOWN = 3;

/** One half of a card's footer: fills its cell so the pair reads as part of the card. */
function FooterLink({ href, label, ariaLabel, icon, primary }: {
  href: string; label: string; ariaLabel: string; icon: ReactNode; primary?: boolean;
}) {
  const base = 'flex items-center justify-center gap-2 px-3 py-3 text-sm font-medium whitespace-nowrap transition-colors';
  if (!href) {
    return (
      <span
        className={`${base} text-gray-400 cursor-not-allowed`}
        title="This workspace's address isn't known yet. It isn't in the account's workspace list under this name."
      >
        {icon}
        {label}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={`${ariaLabel} (opens in a new tab)`}
      className={
        primary
          ? `${base} text-primary hover:bg-primary/5 focus-visible:bg-primary/5 outline-none`
          : `${base} text-gray-700 hover:bg-gray-50 focus-visible:bg-gray-50 outline-none`
      }
    >
      {icon}
      {label}
      <ExternalLink className="w-3.5 h-3.5 opacity-60" aria-hidden="true" />
    </a>
  );
}

function GroupList({ groups }: { groups: string[] }) {
  const shown = groups.slice(0, MAX_GROUPS_SHOWN);
  const rest = groups.slice(MAX_GROUPS_SHOWN);
  return (
    <div className="flex items-start gap-1.5 text-xs text-gray-500">
      <Users className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true" />
      <div className="flex flex-wrap items-center gap-1">
        <span>Through</span>
        {shown.map((g) => (
          <span key={g} className="rounded bg-gray-100 px-1.5 py-0.5 font-medium text-gray-700 break-all">
            {g}
          </span>
        ))}
        {rest.length > 0 && (
          <span className="rounded px-1 py-0.5 text-gray-500" title={rest.join(', ')}>
            +{rest.length} more
          </span>
        )}
      </div>
    </div>
  );
}

function WorkspaceCard({ ws }: { ws: MyWorkspace }) {
  return (
    <Card className="flex flex-col overflow-hidden">
      <CardContent className="pt-5 pb-4 flex flex-col gap-3 flex-1">
        <div className="flex items-start gap-3">
          <div className="p-2.5 rounded-lg bg-primary/10 text-primary shrink-0">
            <DatabricksIcon className="w-5 h-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-gray-900 break-words">{ws.name}</h3>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {ws.environments.map((env) => (
                <span
                  key={env}
                  className="text-[11px] font-medium uppercase tracking-wide rounded px-1.5 py-0.5 bg-gray-100 text-gray-600"
                >
                  {env}
                </span>
              ))}
              {ws.is_admin && (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium uppercase tracking-wide rounded px-1.5 py-0.5 bg-amber-100 text-amber-800">
                  <ShieldCheck className="w-3 h-3" aria-hidden="true" />
                  Admin
                </span>
              )}
            </div>
          </div>
        </div>
        <GroupList groups={ws.groups} />
      </CardContent>
      <div className="mt-auto grid grid-cols-2 divide-x divide-gray-200 border-t border-gray-200">
        <FooterLink
          href={ws.lakehouse_url}
          label="Lakehouse"
          ariaLabel={`Open the lakehouse in ${ws.name}`}
          icon={<DatabricksIcon className="w-4 h-4" />}
          primary
        />
        <FooterLink
          href={ws.genie_one_url}
          label="Genie One"
          ariaLabel={`Open Genie One in ${ws.name}`}
          icon={<GenieIcon className="w-4 h-4" />}
        />
      </div>
    </Card>
  );
}

export function Workspaces() {
  const brandShortName = useBrandingStore((s) => s.brandShortName);
  const accountGenieOneUrl = useBrandingStore((s) => s.genieOneUrl);
  const [data, setData] = useState<MyWorkspaceList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const query = params.get('q') ?? '';

  useEffect(() => {
    let cancelled = false;
    listMyWorkspaces()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load your workspaces.');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const genieOneUrl = data?.genie_one_url || accountGenieOneUrl;
  const workspaces = useMemo(() => data?.workspaces ?? [], [data]);

  // Configured environments first, then any a rule uses that isn't configured.
  const envOptions = useMemo(() => {
    const opts = [...(data?.environments ?? [])];
    for (const ws of workspaces) {
      for (const env of ws.environments) {
        if (!opts.some((o) => o.key === env)) opts.push({ key: env, label: env.toUpperCase() });
      }
    }
    return opts;
  }, [data, workspaces]);
  const env = params.get('env') || data?.default_environment || ALL_ENVIRONMENTS;

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const inEnv = (ws: MyWorkspace) => env === ALL_ENVIRONMENTS || ws.environments.includes(env);
  const needle = query.trim().toLowerCase();
  const matchesQuery = (ws: MyWorkspace) =>
    !needle || [ws.name, ...ws.groups].some((v) => v.toLowerCase().includes(needle));
  const visible = workspaces.filter((ws) => inEnv(ws) && matchesQuery(ws));
  const envLabel = envOptions.find((o) => o.key === env)?.label ?? env.toUpperCase();

  let emptyMessage: ReactNode = null;
  if (data && workspaces.length === 0) {
    if (!data.access_rules_enabled) {
      emptyMessage = 'Workspace access isn’t tracked here yet, so your workspaces can’t be listed.';
    } else if (!data.groups_known) {
      emptyMessage = 'Your groups couldn’t be read, so your workspaces can’t be listed. Try again later.';
    } else {
      emptyMessage = (
        <>
          None of your groups has access to a workspace yet.{' '}
          <Link to="/request" className="text-primary font-medium hover:underline">
            Ask the {brandShortName} Agent
          </Link>{' '}
          to help you request access.
        </>
      );
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Workspaces</h1>
        <p className="text-gray-600">Open the lakehouse, or Genie One, in a workspace you have access to.</p>
      </div>

      <Card>
        <CardContent className="py-6 space-y-5">
          <div className="flex items-start gap-4">
            <div className="p-3 rounded-lg bg-primary/10 text-primary shrink-0">
              <DatabricksIcon className="w-6 h-6" />
            </div>
            <div className="space-y-2">
              <h2 className="text-lg font-semibold text-gray-900">What is the lakehouse?</h2>
              <p className="text-sm text-gray-700 leading-relaxed">
                The lakehouse is where your organization’s data lives and is worked on in Databricks.
                Tables, files, dashboards, notebooks, jobs and models all sit in one governed place, and
                each workspace is a door into it with its own projects and compute. Your access to data is
                the same whichever workspace you open.
              </p>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-gray-200 p-4">
              <div className="flex items-center gap-2 font-medium text-gray-900">
                <DatabricksIcon className="w-4 h-4 text-primary" />
                Open the lakehouse to build
              </div>
              <p className="mt-1.5 text-sm text-gray-600">
                Write SQL or Python, build pipelines and jobs, publish dashboards or train models. Use it
                when you are creating or changing something.
              </p>
            </div>
            <div className="rounded-lg border border-gray-200 p-4">
              <div className="flex items-center gap-2 font-medium text-gray-900">
                <GenieIcon className="w-4 h-4 text-primary" />
                Open Genie One to analyze
              </div>
              <p className="mt-1.5 text-sm text-gray-600">
                Ask questions of your data in plain language and get answers, charts and dashboards back.
                Use it for totals, trends, comparisons and any other analysis.
              </p>
            </div>
          </div>
          <p className="text-sm text-gray-600">
            To find data, check your access or request it, use the{' '}
            <Link to="/request" className="text-primary font-medium hover:underline">
              {brandShortName} Agent
            </Link>{' '}
            or{' '}
            <Link to="/discovery" className="text-primary font-medium hover:underline">
              Discover
            </Link>
            .
            {genieOneUrl && (
              <>
                {' '}To ask questions across every workspace at once, open{' '}
                <a
                  href={genieOneUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary font-medium hover:underline"
                >
                  Genie One for your account
                </a>
                .
              </>
            )}
          </p>
        </CardContent>
      </Card>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold text-gray-900">Your workspaces</h2>
        {error ? (
          <Card className="bg-red-50 border-red-200">
            <CardContent className="py-6 text-sm text-red-700">{error}</CardContent>
          </Card>
        ) : !data ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
            Loading your workspaces…
          </div>
        ) : emptyMessage ? (
          <Card className="bg-gray-50">
            <CardContent className="py-10 text-center text-gray-600">{emptyMessage}</CardContent>
          </Card>
        ) : (
          <>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full sm:max-w-sm">
                <Search className="pointer-events-none absolute left-3 top-1/2 w-4 h-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setParam('q', e.target.value)}
                  placeholder="Search workspaces or groups"
                  aria-label="Search workspaces or groups"
                  className="w-full rounded-md border border-gray-300 bg-white py-2 pl-9 pr-9 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => setParam('q', '')}
                    aria-label="Clear search"
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:text-gray-600"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              {envOptions.length > 0 && (
                <div role="group" aria-label="Environment" className="inline-flex rounded-md border border-gray-300 bg-white p-0.5">
                  {[...envOptions, { key: ALL_ENVIRONMENTS, label: 'All' }].map((o) => {
                    const count = o.key === ALL_ENVIRONMENTS
                      ? workspaces.length
                      : workspaces.filter((ws) => ws.environments.includes(o.key)).length;
                    const active = o.key === env;
                    return (
                      <button
                        key={o.key}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setParam('env', o.key === data.default_environment ? '' : o.key)}
                        className={`rounded px-3 py-1.5 text-xs font-semibold tracking-wide transition-colors ${
                          active ? 'bg-primary text-white' : 'text-gray-600 hover:bg-gray-100'
                        }`}
                      >
                        {o.label}
                        <span className={`ml-1.5 font-normal ${active ? 'text-white/80' : 'text-gray-400'}`}>{count}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            {visible.length === 0 ? (
              <Card className="bg-gray-50">
                <CardContent className="py-10 text-center text-gray-600">
                  {needle ? (
                    <>No workspaces match “{query.trim()}”{env !== ALL_ENVIRONMENTS && <> in {envLabel}</>}.</>
                  ) : (
                    <>None of your workspaces is in {envLabel}.</>
                  )}{' '}
                  {env !== ALL_ENVIRONMENTS && (
                    <button
                      type="button"
                      onClick={() => setParam('env', ALL_ENVIRONMENTS)}
                      className="text-primary font-medium hover:underline"
                    >
                      Show all environments
                    </button>
                  )}
                </CardContent>
              </Card>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {visible.map((ws) => (
                  <WorkspaceCard key={ws.name} ws={ws} />
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
