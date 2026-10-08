import { Suspense } from "react";
import { Loading } from "@/components/ui";
import TenderView from "./TenderView";

export default function TenderPage() {
  return (
    <Suspense fallback={<Loading />}>
      <TenderView />
    </Suspense>
  );
}
