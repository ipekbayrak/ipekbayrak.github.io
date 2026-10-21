# NöbetCep public distribution

Website: https://ipekbayrak.com.tr/nobetcep/

Public feed: https://ipekbayrak.github.io/nobetcep/data/v1

The public site is under `public/nobetcep/`. The Google seller declaration is at `public/app-ads.txt`, served from the hostname root. The scheduler updates licensed İzmir and Kayseri municipal feeds every two hours and preserves failed providers' actual last-good timestamps.

Data: İzmir and Kayseri Metropolitan Municipalities, CC BY 4.0. Each JSON feed carries its source and license URL. Pharmacy data is informational; users should call before visiting. Other provinces link to the official e-Devlet inquiry.

The workflow deploys the complete `public/` directory. Keep other app sites in their own subdirectories and retain the shared Pages concurrency group.
