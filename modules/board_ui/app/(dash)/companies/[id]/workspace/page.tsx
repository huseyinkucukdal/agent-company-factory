"use client";

import { ProjectWorkspaceTree } from "@/components/project-workspace-tree";
import { useParams } from "next/navigation";

export default function CompanyWorkspacePage() {
  const params = useParams<{ id: string }>() ?? { id: "" };

  return (
    <div className="p-6">
      <ProjectWorkspaceTree companyId={params.id} />
    </div>
  );
}
