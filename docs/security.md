# What is public, and what is not

What the pages give away, what is behind the password, and how a
visitor is told apart from another one. The environment variables
themselves are listed in the [README](../README.md#environment-variables).

The pages people are given are read-only, and the API keys are never served.
Everything that writes is under `/admin`. Responses carry a CSP that forbids
inline script, plus `nosniff`, `DENY` framing, `no-referrer` and, over HTTPS,
HSTS. Chart tooltips escape anything server-supplied, and CSV columns starting
with a character a spreadsheet would run as a formula are prefixed.

The session cookie is `Secure`, `HttpOnly` and `SameSite=Lax`. There are no CSRF
tokens: every admin action is a form POST authenticated by that cookie, and
`SameSite=Lax` is what stops another site posting one on a signed-in viewer's
behalf. That is the whole defence, so it is set explicitly rather than left to
the browser's default.

Sign-in failures are counted per address and lock that address out for five
minutes after six of them; a correct password costs nothing, so signing in
often cannot lock you out of your own admin page. The data endpoints allow 600
calls per address per five minutes. Above *that* sits a tripwire on the total
across everyone, which does not slow anything down - it stops, and stays
stopped, writing the latch to the settings file so a restart does not resume
serving. It is deliberately far out of reach: a refused call is never counted,
so one machine can only ever contribute its own 600, and tripping it needs
dozens of addresses at once rather than a busy evening on a shared link.
Exports are five per address per six hours, and twenty a day across everyone.

One endpoint takes writes without a password: `/hook/dink/<token>`, where a
RuneLite plugin reports a login or a logout - see
[The schedule, and what is stored](data.md#session-logins). It sits outside
the tripwire on
purpose - a login that is refused is gone for good, where everything the
tripwire protects can be fetched again - and is capped at thirty calls per
player per five minutes instead.

### Knowing who is calling

All of that depends on telling visitors apart. Behind a proxy `remote_addr` is
the proxy, which pools everyone into one bucket; but a proxy header is only
worth believing if something in front of you overwrites whatever the caller
sent. Trust one unconditionally and it becomes a dial the caller controls -
rotate the header and every request counts as a new person, which buys unlimited
password guesses. So no header is trusted unless you name it:

```bash
WOM_TRUSTED_IP_HEADER=Fly-Client-IP      # Fly.io, which overwrites it
WOM_TRUSTED_IP_HEADER=CF-Connecting-IP   # Cloudflare
WOM_TRUSTED_IP_HEADER=X-Forwarded-For    # nginx, Caddy - the weakest to trust
```

Leave it unset when nothing is in front of the app. `X-Forwarded-For` is a list
the client can prepend to, so its leftmost entry - the one read here - is
client-controlled unless your proxy rewrites the header rather than appending.
