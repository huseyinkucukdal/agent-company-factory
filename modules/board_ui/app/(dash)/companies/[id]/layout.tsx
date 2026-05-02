import { CompanyNav } from "@/components/company-nav";

export default function CompanyLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { id: string };
}) {
  return (
    <>
      <CompanyNav companyId={params.id} />
      {children}
    </>
  );
}
