import { useEffect, useState } from "react";
import { api, getToken } from "@/api";

// One /auth/me per session token, shared by every screen that asks.
let cache = { token: null, promise: null };

export function useMe() {
  const [me, setMe] = useState(null);
  useEffect(() => {
    const token = getToken();
    if (!token) return undefined;
    if (cache.token !== token) cache = { token, promise: api.me() };
    let live = true;
    cache.promise.then((value) => live && setMe(value)).catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  return me;
}
