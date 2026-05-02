"use client";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

interface FileMeta {
  relative_path: string;
  size_bytes: number;
  modified_at: string;
  is_dir?: boolean;
}

interface WorkspaceListing {
  agent_id: string;
  files: FileMeta[];
}

interface WorkspaceFile {
  agent_id: string;
  relative_path: string;
  content: string;
  size_bytes: number;
}

export function ProjectWorkspaceTree({ companyId }: { companyId: string }) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const root = useQuery({
    queryKey: ["company", companyId, "project-workspace", "tree", ""],
    queryFn: () => api<WorkspaceListing>(listEndpoint(companyId)),
    enabled: Boolean(companyId),
    retry: false,
  });
  const fileContent = useQuery({
    queryKey: [
      "company",
      companyId,
      "project-workspace",
      "file",
      selectedPath,
    ],
    queryFn: () =>
      api<WorkspaceFile>(fileEndpoint(companyId, selectedPath ?? "")),
    enabled: Boolean(companyId && selectedPath),
    retry: false,
  });
  const rootFiles = useSortedFiles(root.data?.files);

  return (
    <>
      <Card className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">Project Workspace</h1>
            <div className="text-sm text-muted">Shared company workspace</div>
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => root.refetch()}
            disabled={root.isFetching}
          >
            Refresh
          </Button>
        </div>

        {root.isLoading ? (
          <div className="rounded-md border p-3 text-sm text-muted">
            Loading workspace...
          </div>
        ) : root.error ? (
          <div className="rounded-md border border-red-500/40 p-3 text-sm text-red-600">
            {(root.error as Error).message}
          </div>
        ) : rootFiles.length === 0 ? (
          <div className="rounded-md border p-3 text-sm text-muted">
            No files.
          </div>
        ) : (
          <div className="overflow-hidden rounded-md border">
            <div className="grid grid-cols-[minmax(0,1fr)_92px_156px] gap-3 border-b bg-bg px-3 py-2 text-xs font-medium text-muted">
              <div>Name</div>
              <div className="text-right">Size</div>
              <div>Modified</div>
            </div>
            <WorkspaceRows
              companyId={companyId}
              files={rootFiles}
              depth={0}
              onOpenFile={setSelectedPath}
            />
          </div>
        )}
      </Card>

      <Dialog
        open={selectedPath !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedPath(null);
        }}
      >
        <DialogContent
          title={selectedPath ?? "Workspace file"}
          className="w-[min(92vw,920px)]"
        >
          {fileContent.isLoading ? (
            <div className="rounded-md border p-3 text-sm text-muted">
              Loading file...
            </div>
          ) : fileContent.error ? (
            <div className="rounded-md border border-red-500/40 p-3 text-sm text-red-600">
              {(fileContent.error as Error).message}
            </div>
          ) : (
            <div className="space-y-3">
              <div className="font-mono text-xs text-muted">
                {formatBytes(fileContent.data?.size_bytes ?? 0)}
              </div>
              <pre className="max-h-[62vh] overflow-auto whitespace-pre-wrap rounded-md border bg-bg p-3 font-mono text-xs leading-relaxed">
                {fileContent.data?.content ?? ""}
              </pre>
            </div>
          )}
          <div className="mt-4 flex justify-end">
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                Close
              </Button>
            </DialogClose>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

function WorkspaceRows({
  companyId,
  files,
  depth,
  onOpenFile,
}: {
  companyId: string;
  files: FileMeta[];
  depth: number;
  onOpenFile: (path: string) => void;
}) {
  return (
    <ul className="divide-y">
      {files.map((file) =>
        file.is_dir ? (
          <WorkspaceDirectory
            key={file.relative_path}
            companyId={companyId}
            directory={file}
            depth={depth}
            onOpenFile={onOpenFile}
          />
        ) : (
          <WorkspaceFileRow
            key={file.relative_path}
            file={file}
            depth={depth}
            onOpenFile={onOpenFile}
          />
        ),
      )}
    </ul>
  );
}

function WorkspaceDirectory({
  companyId,
  directory,
  depth,
  onOpenFile,
}: {
  companyId: string;
  directory: FileMeta;
  depth: number;
  onOpenFile: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const children = useQuery({
    queryKey: [
      "company",
      companyId,
      "project-workspace",
      "tree",
      directory.relative_path,
    ],
    queryFn: () =>
      api<WorkspaceListing>(listEndpoint(companyId, directory.relative_path)),
    enabled: expanded,
    retry: false,
  });
  const childFiles = useSortedFiles(children.data?.files);

  return (
    <li>
      <button
        type="button"
        className={rowClassName}
        style={rowIndent(depth)}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        title={directory.relative_path}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="w-4 shrink-0 text-center text-muted">
            {expanded ? "-" : "+"}
          </span>
          <span className="truncate font-mono">
            {fileLabel(directory.relative_path)}/
          </span>
        </span>
        <span className="text-right font-mono text-[11px] text-muted">dir</span>
        <span className="truncate text-xs text-muted">
          {formatDate(directory.modified_at)}
        </span>
      </button>

      {expanded ? (
        <div>
          {children.isLoading ? (
            <div
              className="px-3 py-2 text-sm text-muted"
              style={rowIndent(depth + 1)}
            >
              Loading...
            </div>
          ) : children.error ? (
            <div
              className="px-3 py-2 text-sm text-red-600"
              style={rowIndent(depth + 1)}
            >
              {(children.error as Error).message}
            </div>
          ) : childFiles.length === 0 ? (
            <div
              className="px-3 py-2 text-sm text-muted"
              style={rowIndent(depth + 1)}
            >
              Empty
            </div>
          ) : (
            <WorkspaceRows
              companyId={companyId}
              files={childFiles}
              depth={depth + 1}
              onOpenFile={onOpenFile}
            />
          )}
        </div>
      ) : null}
    </li>
  );
}

function WorkspaceFileRow({
  file,
  depth,
  onOpenFile,
}: {
  file: FileMeta;
  depth: number;
  onOpenFile: (path: string) => void;
}) {
  return (
    <li>
      <button
        type="button"
        className={cn(rowClassName, "text-accent")}
        style={rowIndent(depth)}
        onClick={() => onOpenFile(file.relative_path)}
        title={file.relative_path}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="w-4 shrink-0 text-center text-muted">-</span>
          <span className="truncate font-mono">
            {fileLabel(file.relative_path)}
          </span>
        </span>
        <span className="text-right font-mono text-[11px] text-muted">
          {formatBytes(file.size_bytes)}
        </span>
        <span className="truncate text-xs text-muted">
          {formatDate(file.modified_at)}
        </span>
      </button>
    </li>
  );
}

function useSortedFiles(files: FileMeta[] | undefined) {
  return useMemo(() => {
    return [...(files ?? [])].sort((a, b) => {
      if (Boolean(a.is_dir) !== Boolean(b.is_dir)) return a.is_dir ? -1 : 1;
      return a.relative_path.localeCompare(b.relative_path);
    });
  }, [files]);
}

const rowClassName =
  "grid w-full grid-cols-[minmax(0,1fr)_92px_156px] items-center gap-3 px-3 py-2 text-left text-sm hover:bg-bg";

function rowIndent(depth: number) {
  return { paddingLeft: `${12 + depth * 18}px` };
}

function listEndpoint(companyId: string, path?: string) {
  if (!path) return `/companies/${companyId}/project-workspace`;
  return `/companies/${companyId}/project-workspace?path=${encodeURIComponent(path)}`;
}

function fileEndpoint(companyId: string, path: string) {
  return `/companies/${companyId}/project-workspace/file?path=${encodeURIComponent(path)}`;
}

function fileLabel(path: string) {
  const parts = path.split("/").filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : path;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString();
}
