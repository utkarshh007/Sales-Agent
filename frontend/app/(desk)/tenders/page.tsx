import { Suspense } from "react";
import { Loading } from "@/components/ui";
import TendersView from "./TendersView";

export default function TendersPage() {
  return (
    <Suspense fallback={<Loading />}>
      <TendersView />
    </Suspense>
  );
}
