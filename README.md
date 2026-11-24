<div align="center">

# Grab

**Paste a YouTube link and get the video or the music on your device.**

![](https://img.shields.io/badge/BULK-25_LINKS_AT_ONCE-24292f?style=for-the-badge)
![](https://img.shields.io/badge/SPEED-8_PARALLEL_STREAMS-24292f?style=for-the-badge)
![](https://img.shields.io/badge/PLAYS_EVERYWHERE-H.264_%2B_AAC-24292f?style=for-the-badge)
![](https://img.shields.io/badge/STORES-NOTHING-24292f?style=for-the-badge)

</div>

Grab turns YouTube links into real files — an MP4 you can watch on a plane with no signal, or an MP3 you can load onto a pair of running headphones.

I built it because the websites that do this are buried in pop-ups, make you paste one link at a time, and hand back files my iPhone refuses to open.

### Paste the whole list

Drop in up to 25 links at once, or a playlist, or an entire channel. Every item gets its own progress bar and its own save button, and one button at the bottom takes the whole batch as a zip.

### The files actually play

This is the part most downloaders get wrong. A file ending in `.mp4` is not a promise about what is inside it. The video can be packed in a format Apple devices cannot decode, so you double-click it and QuickTime tells you the file isn't compatible — after you already waited for the download. Grab asks for the combination that iPhones, Macs and Windows all play natively, so what lands on your device just opens.

### Fast on a bad connection

YouTube throttles a single download to a crawl. Grab pulls eight pieces of the file at the same time and reassembles them, which on a throttled line is roughly ten times quicker. If the connection drops halfway through, it resumes from where it stopped instead of starting the file again.

### It keeps nothing

Files go to a temporary folder, stream to you, and are deleted about half an hour later. There is no database, no account, and no record of what you downloaded.

### A Chrome extension as well

Right-click any YouTube video to send it straight to Grab, or scan a page and take every video on it in one go.

## Get it

Grab runs on your own machine instead of on a website, and that is deliberate. YouTube blocks requests coming from data centres, so a hosted copy has to borrow your login cookies to work at all, while a copy running at home needs nothing from you.

```bash
git clone https://github.com/ryan-helou/YTtoMP4.git
cd YTtoMP4
./run.sh
```

It opens in your browser and listens on your local network too, so your phone can use it over the same Wi-Fi. The Chrome extension is in `extension/` and loads unpacked.
