# Tailscale hozzáférés

https://bela-macbook-pro-m4-pro.tail8a282e.ts.net:8443/

A 8443-as HTTPS-port a helyi 127.0.0.1:8765 engine-re továbbít. A Tailscale Serve háttérben fut; a meglévő 443-as szolgáltatás megmaradt. Funnel nincs bekapcsolva. A konfiguráció a config/network.json fájlban van, a szolgáltatás újratelepítésekor is betöltődik.

Ellenőrzött: HTTPS-tanúsítvány, főoldal, JavaScript, health és dashboard API, azonos eredetű POST és SSE-frissítés. Más eredetű írási kérés 403-mal tiltott. Az ellenőrzés erről a Macről történt; a telefonról megnyitáshoz Tailscale-kapcsolat és ébren lévő Mac szükséges.

Kikapcsolás csak ehhez a szolgáltatáshoz:

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --https=8443 off
```
