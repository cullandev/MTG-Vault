# Arena playmats

Six painted backdrops for the Arena's table, one per realm, designed by the
owner in Claude Design and delivered as a package (art, theme data, CSS and an
atmosphere layer). The theme data lives typed in `src/lib/playmats.ts`; the
atmosphere layer is `src/components/Atmosphere.tsx`; its keyframes are in
`src/index.css`.

| id | realm | accent |
| --- | --- | --- |
| greenhollow | Pastoral Shirelands | `#e0b24a` gold |
| silverwood | Elven Forest Realm | `#c9d8c2` pale sage |
| deephold | Dwarven Mountain Halls (default) | `#d68a3a` copper |
| ashenmaw | Volcanic Dark Land | `#ff5a1f` lava |
| kingsfall | Ancient Ruined Kingdom | `#a9b3c4` steel |
| rimehold | Ice-bound North (pale mat, dark ink) | `#2f6fa8` ice |

Each painting is 1312x816 (16:10), sRGB JPEG, drawn to the package's
composition rules: horizon at about 42% of the height, the centre band open and
low-contrast so cards sit on calm ground, hero elements in the outer fifths and
the top third, nothing important in the top 10% or bottom 15%. The table's seam
lands on the horizon. The service worker caches these like hashed assets.

To add a realm: drop `<id>.jpg` here, add its entry to `REALMS`, and give
`Atmosphere.tsx` a weather for it (or let it fall to the default).
