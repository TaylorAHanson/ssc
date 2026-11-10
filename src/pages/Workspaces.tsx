/**
 * Workspaces — what the lakehouse is, when to use it versus Genie One, and a
 * card per Databricks workspace the user's groups can use (from the Access
 * Rules), each opening that workspace or its Genie One.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ExternalLink, Loader2, ShieldCheck, Users } from 'lucide-react';
import { Card, CardContent } from '../components/ui/card';
import { DatabricksIcon, GenieIcon } from '../components/icons/DatabricksIcons';
import { useBrandingStore } from '../stores/brandingStore';
import { listMyWorkspaces, type MyWorkspaceList } from '../services/api';

const linkButton =
  'inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium h-9 px-3 transition-colors';

function OpenLink({ href, label, icon, primary }: { href: string; label: string; icon: ReactNode; primary?: boolean }) {
  if (!href) {
    return (
      <span
        className={`${linkButton} border border-gray-200 text-gray-400 cursor-not-allowed`}
        title="This workspace's address isn't known yet"
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
      className={
        primary
          ? `${linkButton} bg-primary text-white hover:bg-primary/90`
          : `${linkButton} border border-gray-300 bg-white text-gray-800 hover:bg-gray-50`
      }
    >
      {icon}
      {label}
      <ExternalLink className="w-3.5 h-3.5 opacity-70" aria-hidden="true" />
    </a>
  );
}

export function Workspaces() {
  const brandShortName = useBrandingStore((s) => s.brandShortName);
  const accountGenieOneUrl = useBrandingStore((s) => s.genieOneUrl);
  const [data, setData] = useState<MyWorkspaceList | null>(null);
  const [error, setError] = useState<string | null>(null);

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
  const workspaces = data?.workspaces ?? [];

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
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {workspaces.map((ws) => (
              <Card key={ws.name} className="flex flex-col">
                <CardContent className="py-5 flex flex-col gap-4 flex-1">
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
                  <p className="flex items-start gap-1.5 text-xs text-gray-500">
                    <Users className="w-3.5 h-3.5 mt-px shrink-0" aria-hidden="true" />
                    <span>Through {ws.groups.join(', ')}</span>
                  </p>
                  <div className="mt-auto flex flex-wrap gap-2">
                    <OpenLink
                      href={ws.lakehouse_url}
                      label="Open Lakehouse"
                      icon={<DatabricksIcon className="w-4 h-4" />}
                      primary
                    />
                    <OpenLink href={ws.genie_one_url} label="Open Genie One" icon={<GenieIcon className="w-4 h-4" />} />
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
