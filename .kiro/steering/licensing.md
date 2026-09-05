# Licensing (steering)

Understand licensing is the user's and the vendor's business. No agent working on this
project debugs, inspects, or changes it.

- Never run `und license`, `und -isundlicensed`, `und -licensehowmanydays`, or any
  `und -*license*` / `-*offline*` / `-*nodelock*` switch by hand. Never read or edit
  `~/.config/SciTools/License.conf`.
- The tool itself calls `und -isundlicensed` inside `doctor` and the licence check of a run,
  and no other licence command (user's instruction, 2026-09-05: "throwing an error early is
  enough"). That code path is tested with fakes and is not to be exercised interactively "to
  see". A licence without the API option is caught by `doctor`'s analysis probe opening its
  scratch database through the API, and by a check at its first metric read (exit 4).
- Measured 2026-09-05 on Understand 8.0 (Build 1262): an agent's `und license`,
  `-licensehowmanydays` and `-showofflinerequestcode` rewrote `License.conf` and removed the
  stored offline reply code. `-show…` was not read-only. The fix was the user's, with the vendor.
- On a licensing failure (`No Server Response`, `Licensing Error`, `NoApiLicense`,
  `license is Invalid`): quote the exact `und` output, stop, and hand it to the user with
  the vendor's page -- <https://docs.scitools.com/help/licensing/command-line-licensing.html>.
  Licensing is done from the command line, by the user. Do not retry, do not probe, do not
  run the commands on that page yourself.
- This machine is kept off the network for licensing reasons; that boundary is not negotiable
  and is not the agent's to relax (see `tech.md`).
