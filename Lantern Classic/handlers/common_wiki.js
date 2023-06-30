// For the legacy fandom scraper
const cheerio = require("cheerio")

const wikipediaRegex = /https*:\/\/([a-z]{2,}|[a-z]{2,}-[a-z]{2,})\.m*\.*(wikipedia|wikivoyage|wikiquote|wikiversity)\.org\/wiki\/(.*)/g

module.exports = {
  fetchRandomWikipedia: async (langCode, wikiType) => {
    const wikiName = wikiType ? wikiType : "wikipedia"
    const response = await fetch(`https://${langCode ? langCode : "en"}.${wikiName}.org/api/rest_v1/page/random/summary`)
    if (!response.ok)
      throw `Request error, attempted to access ${response.url}`
    
    const body = await response.json()
  
    let genericImage = "https://www.wikipedia.org/portal/wikipedia.org/assets/img/Wikipedia-logo-v2.png"
    switch (wikiType) {
      case "wikiversity":
        genericImage = "https://www.wikiversity.org/portal/wikiversity.org/assets/img/Wikiversity-logo-tiles_1x.png"
        break
      case "wikivoyage":
        genericImage = "https://www.wikivoyage.org/portal/wikivoyage.org/assets/img/Wikivoyage-logo-tiles_1x.png"
        break
      case "wikiquote":
        genericImage = "https://www.wikiquote.org/portal/wikiquote.org/assets/img/Wikiquote-logo-tiles_1x.png"
        break
    }
    const parsedContent = {
      title: body.title,
      thumbnail: body.thumbnail ? body.thumbnail.source : genericImage,
      url: body.content_urls.desktop.page,
      content: body.extract ? body.extract : "No summary available",
      wikiType: wikiName.charAt(0).toUpperCase() + wikiName.slice(1)
    }
    return parsedContent
  },
  fetchWikipedia: async (pageUrl) => {
    const urlData = wikipediaRegex.exec(pageUrl)
    wikipediaRegex.lastIndex = 0

    if (urlData !== null) {
      const response = await fetch(`https://${urlData[1]}.${urlData[2]}.org/api/rest_v1/page/summary/${urlData[3]}`)
      if (!response.ok)
        throw `Request error, attempted to access ${response.url}`
      
      let genericImage = "https://www.wikipedia.org/portal/wikipedia.org/assets/img/Wikipedia-logo-v2.png"
      switch (urlData[2]) {
        case "wikiversity":
          genericImage = "https://www.wikiversity.org/portal/wikiversity.org/assets/img/Wikiversity-logo-tiles_1x.png"
          break
        case "wikivoyage":
          genericImage = "https://www.wikivoyage.org/portal/wikivoyage.org/assets/img/Wikivoyage-logo-tiles_1x.png"
          break
        case "wikiquote":
          genericImage = "https://www.wikiquote.org/portal/wikiquote.org/assets/img/Wikiquote-logo-tiles_1x.png"
          break
      }
      const body = await response.json()
      let wikiName = urlData[2].toLowerCase()

      wikiName = wikiName.charAt(0).toUpperCase()
      + wikiName.slice(1)
      //console.log(JSON.stringify(body))
      return {
        title: body.title,
        thumbnail: body.thumbnail ? body.thumbnail.source : genericImage,
        url: body.content_urls.desktop.page,
        content: body.extract ? body.extract : "No summary available",
        wikiType: wikiName
      }
    } else {
      return null
    }
  },
  legacy: {
    scrapeWiki: async (wikiUri, genericPicUri) => {
      let response
      if (wikiUri === undefined) {
        response = await fetch(wikipediaUris[0])
      } else {
        response = await fetch(wikiUri)
      }
      const pageUri = response.url
      const body = await response.text()
      //console.dir(body)
      const $ = cheerio.load(body)
    
      // get (hopefully) lowest quality photo
      const picUri = $("head > meta[property=og:image]").last().prop("content")
        ? $("head > meta[property=og:image]").last().prop("content")
        : genericPicUri
    
      // remove [1] (references)
      $(".reference").remove()
    
      // some articles become blank bc of that
      $(".mw-empty-elt").remove()
      $("#coordinates").parent().parent().remove()
    
      // LGBTA+ wiki has a different id for the title
      const pageName = $("#cosmos-title-text").text().trim()
        ? $("#cosmos-title-text").text().trim()
        : $("#firstHeading").text().trim()
    
      // Get the first paragraph of wikipedia aritcle
      const para = $("#mw-content-text > div.mw-parser-output")
        .children()
        .closest("p")
        .first()
        .text()
        .replace("[edit]", "")
      return [pageUri, pageName, picUri, para ? para : "No summary available"]
    }
  }
}