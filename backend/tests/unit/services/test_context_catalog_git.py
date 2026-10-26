"""Context Catalog Git sync: serialization, env -> branch mapping, push/pull, and the editing guard.

GitHub is replaced by an in-memory repository (``FakeRepo``) with the same
methods as ``GitHubRepoClient``. Each test gets a private in-memory database,
since the sync commits and rolls back its own transactions.
"""
import hashlib

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db  # noqa: F401  (registers every model on Base.metadata)
from app.api.v1 import context_catalog as catalog_api
from app.core.config import settings
from app.db.base import Base
from app.db.context_catalog import ContextChunkModel, ContextDocumentModel, ContextDomainModel
from app.db.context_catalog_git import ContextCatalogGitStateModel
from app.db.session import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.providers.context_catalog.git_repo import GitHubRepoClient, parse_repo
from app.services import context_catalog_git as git
from app.services.context_catalog_service import ContextCatalogService as Svc

BASE = "context-catalog"
EDITOR = User(id="ed@corp.com", email="ed@corp.com", full_name="Ed Itor", roles=["Platform Admin"],
              entitlements=[], is_active=True)
OTHER = User(id="al@corp.com", email="al@corp.com", full_name="Al Ter", roles=["Platform Admin"],
             entitlements=[], is_active=True)
DEFAULT_BRANCHES = [
    {"environment": "dev", "aliases": "", "branch": "dev", "push": True},
    {"environment": "test", "aliases": "tst, qa", "branch": "test", "push": True},
    {"environment": "stage", "aliases": "stg, staging", "branch": "prod", "push": False},
    {"environment": "prod", "aliases": "prd", "branch": "prod", "push": True},
]


# ---------------------------------------------------------------------------
# Fixtures and fakes
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _git_settings(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "dev")
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_GIT_ENABLED", True)
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_GIT_REPO", "acme/catalog")
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_GIT_BRANCHES", [dict(r) for r in DEFAULT_BRANCHES])
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_GIT_BASE_PATH", BASE)
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_DIRECT_EDITING_ENABLED", True)
    monkeypatch.setattr(settings, "GIT_USERNAME", "Catalog Bot")
    monkeypatch.setattr(settings, "GIT_EMAIL", "bot@corp.com")


class FakeRepo:
    """An in-memory GitHub repo: commits hold full ``path -> content`` snapshots."""

    def __init__(self, files=None, branch="main"):
        self.trees = {}
        self.commits = {}
        self.branches = {}
        self.races = 0
        self.writes = 0
        root = self._commit({}, None, "init", {"name": "x", "email": "x@corp.com"})
        self.branches["main"] = root
        if files is not None:
            self.push_external(branch, files, "seed")

    def _tree(self, files):
        sha = hashlib.sha1(repr(sorted(files.items())).encode()).hexdigest()
        self.trees[sha] = dict(files)
        return sha

    def _commit(self, files, parent, message, author, committer=None):
        tree = self._tree(files)
        sha = hashlib.sha1(f"{tree}{parent}{message}{len(self.commits)}".encode()).hexdigest()
        self.commits[sha] = {"tree": tree, "parent": parent, "message": message,
                             "author": author, "committer": committer or author}
        return sha

    def files(self, branch):
        return dict(self.trees[self.commits[self.branches[branch]]["tree"]])

    def head(self, branch):
        return self.commits[self.branches[branch]]

    def push_external(self, branch, changes, message="external"):
        """A commit made in Git (a merged PR): ``changes`` maps path -> content, or None to delete."""
        base = self.branches.get(branch) or self.branches["main"]
        files = dict(self.trees[self.commits[base]["tree"]])
        for path, content in changes.items():
            if content is None:
                files.pop(path, None)
            else:
                files[path] = content
        self.branches[branch] = self._commit(files, base, message, {"name": "dev", "email": "dev@corp.com"})

    # --- GitHubRepoClient surface

    def get_branch_head(self, branch):
        sha = self.branches.get(branch)
        return (sha, self.commits[sha]["tree"]) if sha else None

    def get_default_branch(self):
        return "main"

    def create_branch(self, branch, from_sha):
        self.branches[branch] = from_sha

    def list_blobs(self, tree_sha):
        return {p: git.blob_sha(c) for p, c in self.trees[tree_sha].items()}

    def read_files(self, commit_sha, wanted):
        files = self.trees[self.commits[commit_sha]["tree"]]
        return {p: files[p] for p in wanted}

    def create_tree(self, base_tree, entries):
        files = dict(self.trees[base_tree])
        for entry in entries:
            if "content" in entry:
                files[entry["path"]] = entry["content"]
            else:
                files.pop(entry["path"], None)
        return self._tree(files)

    def create_commit(self, message, tree, parent, author, committer):
        sha = hashlib.sha1(f"{tree}{parent}{message}{len(self.commits)}".encode()).hexdigest()
        self.commits[sha] = {"tree": tree, "parent": parent, "message": message,
                             "author": author, "committer": committer}
        return sha

    def update_branch(self, branch, sha):
        if self.races:
            self.races -= 1
            self.push_external(branch, {f"{BASE}/ops/racer.md": "---\ntitle: Racer\n---\n\nwon the race\n"})
        if self.commits[sha]["parent"] != self.branches.get(branch):
            return False
        self.branches[branch] = sha
        self.writes += 1
        return True


def _sync(db, repo, trigger="manual"):
    cfg = git.resolve_config()
    assert cfg.active, cfg.problem
    return git.sync_catalog(db, cfg, repo, trigger=trigger)


def _seed(db):
    parent = Svc.create_domain(db, name="GTM Operations", description="Top level",
                               reviewers=["r@corp.com"], categories=["Sales"])
    child = Svc.create_domain(db, name="SSA", parent_id=parent.id, primary_owner="a@corp.com")
    playbook = Svc.create_document(
        db, domain_id=child.id, title="Playbook", body_markdown="# Playbook\n\nLakehouse 101.",
        tags=["gtm", "ssa"], source_url="https://example.com/p", created_by="c@corp.com", owner="o@corp.com",
    )
    draft = Svc.create_document(db, domain_id=parent.id, title="Draft notes", body_markdown="rough",
                                status="draft")
    return parent, child, playbook, draft


def _doc(db, title):
    return db.query(ContextDocumentModel).filter(ContextDocumentModel.title == title).one_or_none()


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_render_lays_out_folders_and_front_matter(db):
    parent, child, playbook, _ = _seed(db)
    files = git.render_catalog(db).files

    assert set(files) == {
        f"{parent.slug}/_domain.yml",
        f"{parent.slug}/draft-notes.md",
        f"{parent.slug}/{child.slug}/_domain.yml",
        f"{parent.slug}/{child.slug}/playbook.md",
    }
    meta, body = git.parse_document(files[f"{parent.slug}/{child.slug}/playbook.md"])
    assert meta["id"] == playbook.id
    assert meta["tags"] == ["gtm", "ssa"]
    assert meta["owner"] == "o@corp.com"
    assert meta["source_url"] == "https://example.com/p"
    assert body == "# Playbook\n\nLakehouse 101."
    domain = git.parse_domain(files[f"{parent.slug}/_domain.yml"])
    assert domain["reviewers"] == ["r@corp.com"] and domain["categories"] == ["Sales"]


def test_round_trip_db_to_files_to_db(db):
    _seed(db)
    extras = {}
    before = git.render_catalog(db, extras).files
    ids = {d.title: d.id for d in db.query(ContextDocumentModel).all()}

    db.query(ContextChunkModel).delete()
    db.query(ContextDocumentModel).delete()
    db.query(ContextDomainModel).update({ContextDomainModel.parent_id: None})
    db.query(ContextDomainModel).delete()
    db.commit()

    applier = git._Applier(db, extras, "git:dev")
    applier.apply(before, set(), set(before), full=True)
    db.commit()

    assert applier.report.errors == []
    assert git.render_catalog(db, extras).files == before
    assert {d.title: d.id for d in db.query(ContextDocumentModel).all()} == ids
    # Published documents are searchable again; drafts aren't chunked.
    playbook = _doc(db, "Playbook")
    assert db.query(ContextChunkModel).filter(ContextChunkModel.document_id == playbook.id).count() > 0


def test_unknown_front_matter_survives_a_round_trip(db):
    _, child, playbook, _ = _seed(db)
    extras = {}
    path = next(p for p in git.render_catalog(db).files if p.endswith("playbook.md"))
    text = f"---\nid: {playbook.id}\ntitle: Playbook\nreview_cycle: quarterly\n---\n\nNew body\n"
    git._Applier(db, extras, "git:dev").apply({path: text}, set(), {path})
    db.commit()

    meta, body = git.parse_document(git.render_catalog(db, extras).files[path])
    assert meta["review_cycle"] == "quarterly"
    assert body == "New body"


def test_document_without_front_matter_takes_title_from_heading():
    meta, body = git.parse_document("# Onboarding guide\n\nStep one.\n", "ops/whatever.md")
    assert meta["title"] == "Onboarding guide"
    assert body.startswith("# Onboarding guide")
    meta, _ = git.parse_document("no heading here", "ops/release-checklist.md")
    assert meta["title"] == "release checklist"


def test_title_clash_gets_id_suffix(db):
    domain = Svc.create_domain(db, name="Ops")
    first = Svc.create_document(db, domain_id=domain.id, title="FAQ", body_markdown="a")
    second = Svc.create_document(db, domain_id=domain.id, title="faq", body_markdown="b")
    files = git.render_catalog(db).files
    assert f"ops/faq.md" in files
    assert f"ops/faq-{second.id[:8]}.md" in files
    assert git.parse_document(files["ops/faq.md"])[0]["id"] == first.id


# ---------------------------------------------------------------------------
# Environment -> branch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env,branch,push", [
    ("dev", "dev", True),
    ("TEST", "test", True),
    ("qa", "test", True),
    ("stage", "prod", False),
    ("stg", "prod", False),
    ("prod", "prod", True),
    ("prd", "prod", True),
])
def test_resolve_branch(env, branch, push):
    row = git.resolve_branch(env, DEFAULT_BRANCHES)
    assert (row["branch"], row["push"]) == (branch, push)


@pytest.mark.parametrize("env", ["development", "production", "", "sandbox"])
def test_unmapped_environments_do_not_sync(env, monkeypatch):
    assert git.resolve_branch(env, DEFAULT_BRANCHES) is None
    monkeypatch.setattr(settings, "ENVIRONMENT", env)
    cfg = git.resolve_config()
    assert not cfg.active and "No branch is mapped" in cfg.problem


def test_stage_config_reads_prod_without_push(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "stage")
    cfg = git.resolve_config()
    assert cfg.active and cfg.branch == "prod" and cfg.push_enabled is False


def test_disabled_or_unconfigured_is_inactive(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_GIT_ENABLED", False)
    assert not git.resolve_config().active
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_GIT_ENABLED", True)
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_GIT_REPO", "not a repo")
    assert "repository" in git.resolve_config().problem


@pytest.mark.parametrize("value,full_name,api", [
    ("acme/catalog", "acme/catalog", "https://api.github.com"),
    ("https://github.com/acme/catalog.git", "acme/catalog", "https://api.github.com"),
    ("git@github.com:acme/catalog.git", "acme/catalog", "https://api.github.com"),
    ("https://ghe.corp.com/acme/catalog", "acme/catalog", "https://ghe.corp.com/api/v3"),
])
def test_parse_repo(value, full_name, api):
    ref = parse_repo(value)
    assert (ref.full_name, ref.api_url) == (full_name, api)


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------

def test_first_sync_creates_branch_and_commits_catalog(db):
    parent, child, _, _ = _seed(db)
    repo = FakeRepo()

    result = _sync(db, repo)

    assert result["ok"] and result["commit"]
    files = repo.files("dev")
    assert f"{BASE}/{parent.slug}/{child.slug}/playbook.md" in files
    assert repo.head("dev")["committer"]["email"] == "bot@corp.com"
    # Nothing changed since: the next sync commits nothing.
    writes = repo.writes
    assert _sync(db, repo)["commit"] is None
    assert repo.writes == writes


def test_save_commits_with_editor_as_author(db):
    _, _, playbook, _ = _seed(db)
    repo = FakeRepo()
    _sync(db, repo)

    Svc.update_document(db, playbook.id, body_markdown="Updated body")
    git.record_change(db, EDITOR, "Update", 'document "Playbook"')
    Svc.update_document(db, playbook.id, tags=["gtm"])
    git.record_change(db, OTHER, "Update", 'document "Playbook"')
    assert git.has_pending_changes(db)

    result = _sync(db, repo, trigger="save")

    head = repo.head("dev")
    assert result["commit"] == repo.branches["dev"]
    assert head["author"]["email"] == "al@corp.com"  # most recent editor
    assert head["committer"]["email"] == "bot@corp.com"
    assert "Co-authored-by: Ed Itor <ed@corp.com>" in head["message"]
    assert "2 changes" in head["message"].splitlines()[0]
    assert "M context-catalog/" in head["message"]
    path = next(p for p in repo.files("dev") if p.endswith("playbook.md"))
    assert "Updated body" in repo.files("dev")[path]
    assert not git.has_pending_changes(db)
    state = db.get(ContextCatalogGitStateModel, "dev")
    assert state.last_commit_sha == result["commit"] and state.last_error is None


def test_save_before_first_sync_keeps_its_author(db):
    _seed(db)
    git.record_change(db, EDITOR, "Create", 'document "Playbook"')

    repo = FakeRepo()
    _sync(db, repo)

    assert repo.head("dev")["author"]["email"] == "ed@corp.com"
    assert 'create document "Playbook"' in repo.head("dev")["message"].splitlines()[0]
    assert not git.has_pending_changes(db)


def test_rename_and_delete_are_committed(db):
    _, _, playbook, draft = _seed(db)
    repo = FakeRepo()
    _sync(db, repo)

    Svc.update_document(db, playbook.id, title="Sales playbook")
    Svc.delete_document(db, draft.id)
    _sync(db, repo)

    names = {p.rsplit("/", 1)[-1] for p in repo.files("dev")}
    assert "sales-playbook.md" in names
    assert "playbook.md" not in names and "draft-notes.md" not in names


def test_record_change_is_noop_without_push(db, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "stage")
    git.record_change(db, EDITOR, "Update", "x")
    assert db.get(ContextCatalogGitStateModel, "stage") is None
    monkeypatch.setattr(settings, "ENVIRONMENT", "dev")
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_GIT_ENABLED", False)
    git.record_change(db, EDITOR, "Update", "x")
    assert db.get(ContextCatalogGitStateModel, "dev") is None


def test_branch_moving_during_commit_is_retried(db):
    _, _, playbook, _ = _seed(db)
    repo = FakeRepo()
    _sync(db, repo)
    Svc.update_document(db, playbook.id, body_markdown="Mine")
    repo.races = 1

    result = _sync(db, repo)

    assert result["ok"]
    files = repo.files("dev")
    assert any("Mine" in c for p, c in files.items() if p.endswith("playbook.md"))
    assert f"{BASE}/ops/racer.md" in files  # the other commit is kept, not overwritten
    assert _doc(db, "Racer") is not None  # ...and imported


def test_conflict_keeps_app_version_and_is_reported(db):
    _, _, playbook, _ = _seed(db)
    repo = FakeRepo()
    _sync(db, repo)
    path = next(p for p in repo.files("dev") if p.endswith("playbook.md"))

    repo.push_external("dev", {path: repo.files("dev")[path].replace("Lakehouse 101.", "Git edit")})
    Svc.update_document(db, playbook.id, body_markdown="App edit")
    result = _sync(db, repo)

    assert result["conflicts"] == [path]
    assert "App edit" in repo.files("dev")[path]
    assert _doc(db, "Playbook").body_markdown == "App edit"
    assert db.get(ContextCatalogGitStateModel, "dev").conflicts[0]["path"] == path


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

def test_pull_applies_create_update_delete(db):
    parent, child, playbook, draft = _seed(db)
    repo = FakeRepo()
    _sync(db, repo)
    files = repo.files("dev")
    play_path = next(p for p in files if p.endswith("playbook.md"))
    draft_path = next(p for p in files if p.endswith("draft-notes.md"))

    repo.push_external("dev", {
        play_path: files[play_path].replace("Lakehouse 101.", "Edited in a PR"),
        draft_path: None,
        f"{BASE}/support/_domain.yml": "name: Support\ndescription: Help desk\n",
        f"{BASE}/support/runbook.md": "---\ntitle: Runbook\ntags: [ops]\n---\n\nRestart it.\n",
    })
    result = _sync(db, repo, trigger="schedule")

    pulled = result["pulled"]
    assert _doc(db, "Playbook").body_markdown == "# Playbook\n\nEdited in a PR"
    assert _doc(db, "Draft notes") is None
    runbook = _doc(db, "Runbook")
    assert runbook is not None and runbook.tags == ["ops"]
    support = db.query(ContextDomainModel).filter(ContextDomainModel.slug == "support").one()
    assert support.description == "Help desk" and runbook.domain_id == support.id
    assert pulled["documents"]["deleted"] == ["Draft notes"]
    assert "support" in pulled["domains"]["created"]
    # The new file gains its id in Git, so later edits match it by id.
    assert f"id: {runbook.id}" in repo.files("dev")[f"{BASE}/support/runbook.md"]
    assert _sync(db, repo)["commit"] is None


def test_move_between_folders_in_git_moves_document(db):
    parent, child, playbook, _ = _seed(db)
    repo = FakeRepo()
    _sync(db, repo)
    files = repo.files("dev")
    old = next(p for p in files if p.endswith("playbook.md"))
    new = f"{BASE}/{parent.slug}/playbook.md"
    repo.push_external("dev", {old: None, new: files[old]})

    _sync(db, repo)

    doc = db.get(ContextDocumentModel, playbook.id)
    assert doc is not None and doc.domain_id == parent.id


def test_first_sync_adopts_existing_branch_and_rekeys_by_title(db):
    domain = Svc.create_domain(db, name="Ops")
    local = Svc.create_document(db, domain_id=domain.id, title="FAQ", body_markdown="old")
    only_here = Svc.create_document(db, domain_id=domain.id, title="Local only", body_markdown="x")
    repo = FakeRepo({
        f"{BASE}/ops/_domain.yml": "name: Ops\n",
        f"{BASE}/ops/faq.md": "---\nid: shared-faq-id\ntitle: FAQ\n---\n\nfrom git\n",
    }, branch="dev")

    _sync(db, repo)

    assert db.get(ContextDocumentModel, local.id) is None
    adopted = db.get(ContextDocumentModel, "shared-faq-id")
    assert adopted.body_markdown == "from git"
    # App-only content is committed in the same first sync.
    assert f"{BASE}/ops/local-only.md" in repo.files("dev")
    assert db.get(ContextDocumentModel, only_here.id) is not None


def test_broken_file_in_git_is_reported_not_deleted(db):
    _seed(db)
    repo = FakeRepo()
    _sync(db, repo)
    bad = f"{BASE}/ops/bad.md"
    repo.push_external("dev", {bad: "---\ntitle: [unclosed\n---\nbody\n"})

    first = _sync(db, repo)
    second = _sync(db, repo)

    assert first["pulled"]["errors"][0]["path"] == "ops/bad.md"
    assert second["pulled"]["errors"][0]["path"] == "ops/bad.md"
    assert bad in repo.files("dev")


# ---------------------------------------------------------------------------
# Read-only mirror (stage reads prod)
# ---------------------------------------------------------------------------

def _prod_repo():
    return FakeRepo({
        f"{BASE}/ops/_domain.yml": "name: Ops\n",
        f"{BASE}/ops/faq.md": "---\nid: faq-1\ntitle: FAQ\n---\n\nprod answer\n",
        f"{BASE}/ops/guide.md": "---\nid: guide-1\ntitle: Guide\n---\n\nprod guide\n",
    }, branch="prod")


def test_stage_mirrors_prod_and_never_pushes(db, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "stage")
    stray = Svc.create_domain(db, name="Stage only")
    repo = _prod_repo()
    prod_head = repo.branches["prod"]

    _sync(db, repo)
    assert db.get(ContextDocumentModel, "faq-1").body_markdown == "prod answer"
    assert db.get(ContextDomainModel, stray.id) is None

    # A local edit is reverted; a deletion in prod is applied.
    Svc.update_document(db, "faq-1", body_markdown="stage edit")
    repo.push_external("prod", {f"{BASE}/ops/guide.md": None})
    prod_head = repo.branches["prod"]
    _sync(db, repo)

    assert db.get(ContextDocumentModel, "faq-1").body_markdown == "prod answer"
    assert db.get(ContextDocumentModel, "guide-1") is None
    assert repo.writes == 0 and repo.branches["prod"] == prod_head


def test_mirror_refuses_to_wipe_catalog_when_branch_is_empty(db, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "stage")
    _seed(db)
    repo = FakeRepo({}, branch="prod")

    result = _sync(db, repo)

    assert not result["ok"] and "Refusing" in result["error"]
    assert _doc(db, "Playbook") is not None
    assert "Refusing" in db.get(ContextCatalogGitStateModel, "stage").last_error


def test_missing_branch_in_read_only_env_is_an_error(db, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "stage")
    result = _sync(db, FakeRepo())
    assert not result["ok"] and "does not exist" in result["error"]


def test_status_reports_last_commit_and_pending(db):
    _seed(db)
    repo = FakeRepo()
    _sync(db, repo)
    git.record_change(db, EDITOR, "Create", 'document "X"')

    status = git.get_status(db)["git"]

    assert status["active"] and status["branch"] == "dev" and status["push_enabled"]
    assert status["last_commit_sha"] == repo.branches["dev"]
    assert status["last_commit_url"].endswith(repo.branches["dev"])
    assert status["pending_count"] == 1
    assert status["branch_url"] == "https://github.com/acme/catalog/tree/dev/context-catalog"


# ---------------------------------------------------------------------------
# GitHub client error mapping
# ---------------------------------------------------------------------------

def test_client_maps_non_fast_forward_and_auth_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(422, json={"message": "Update is not a fast forward"})
        return httpx.Response(401, json={"message": "Bad credentials"})

    client = GitHubRepoClient(parse_repo("acme/catalog"), "t", transport=httpx.MockTransport(handler))
    assert client.update_branch("dev", "abc") is False
    with pytest.raises(git.GitSyncError, match="token"):
        client.get_branch_head("dev")


# ---------------------------------------------------------------------------
# Direct-editing guard (API)
# ---------------------------------------------------------------------------

@pytest.fixture
def api(db):
    app = FastAPI()
    app.include_router(catalog_api.router, prefix="/context")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: EDITOR
    return TestClient(app)


def test_direct_editing_off_blocks_writes_but_not_reads(api, db, monkeypatch):
    domain = Svc.create_domain(db, name="Ops")
    monkeypatch.setattr(settings, "CONTEXT_CATALOG_DIRECT_EDITING_ENABLED", False)

    for method, url, body in [
        ("post", "/context/domains", {"name": "New"}),
        ("put", f"/context/domains/{domain.id}", {"name": "Renamed"}),
        ("delete", f"/context/domains/{domain.id}", None),
        ("post", f"/context/domains/{domain.id}/documents", {"title": "T", "body_markdown": "b"}),
        ("post", "/context/import/bundle", {"bundle": {"format": "x", "domains": []}}),
    ]:
        response = api.request(method, url, json=body)
        assert response.status_code == 403, url
        assert "'dev' branch" in response.json()["detail"]
        assert "https://github.com/acme/catalog/tree/dev/context-catalog" in response.json()["detail"]

    assert api.get("/context/domains").status_code == 200
    assert db.get(ContextDomainModel, domain.id).name == "Ops"


def test_direct_editing_on_records_change_for_commit(api, db):
    response = api.post("/context/domains", json={"name": "Ops"})
    assert response.status_code == 200
    domain_id = response.json()["id"]
    response = api.post(f"/context/domains/{domain_id}/documents", json={"title": "FAQ", "body_markdown": "b"})
    assert response.status_code == 200

    pending = db.get(ContextCatalogGitStateModel, "dev").pending_changes
    assert [(c["action"], c["subject"], c["email"]) for c in pending] == [
        ("Create", 'domain "Ops"', "ed@corp.com"),
        ("Create", 'document "FAQ"', "ed@corp.com"),
    ]


def test_record_change_failure_never_breaks_the_save(api, db, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("git down")

    monkeypatch.setattr(git, "_load_state", boom)
    assert api.post("/context/domains", json={"name": "Ops"}).status_code == 200
