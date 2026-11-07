// Candidate names with domain checks run on 2026-11-06 (RDAP via rdap.org; .io via whois.nic.io because RDAP misses .io).
(function () {
  var N = [
    { n: "Labpost", tag: "推荐", story: "实验室和研究者发的帖子。短，一听就懂，不带 git，对各来源中性。", free: ["labpost.org", "labpost.pub"], taken: ["labpost.com"],
      con: "\"lab\" 偏实验科学，独立研究者和人文学者可能觉得不是在说自己。" },
    { n: "Openshelf", tag: "推荐", story: "开放的书架：大家把看到的研究放上来。有\"社区一起摆\"的感觉，产品感强。", free: ["openshelf.pub"], taken: [],
      con: "没体现\"研究\"；要靠副标题说明。.org / .com 还没查。" },
    { n: "Livepaper", story: "会动的论文：最初的定位就是交互式研究报告。", free: ["livepaper.pub", "livepapers.org"], taken: ["livepaper.org"],
      con: "解读、普通博客不算 paper，名字会把范围收窄。" },
    { n: "Branchline", story: "铁路支线，也是 git 分支：原文是主线，回应是支线。概念独特。", free: ["branchline.pub"], taken: ["branchline.org（2026-06 刚被注册）"],
      con: "不说明就看不出和研究有关。" },
    { n: "Readout", story: "实验室里的\"读数\"，也是\"读出来\"。科学感强，好记。", free: ["readout.pub"], taken: [],
      con: "常用词，重名和商标可能多。" },
    { n: "GitPost", story: "现在的名字。git 代表公开的编辑记录，和开源定位一致。", free: ["gitpost.pub", "gitpost.net"], taken: ["gitpost.com", "gitpost.org", "gitpost.io", "gitpost.dev", "gitpost.blog", "gitpost.xyz"],
      con: "有人在系统地持有这个名字：.org/.blog/.xyz 同一天（2024-10-11）注册，.dev、.io 也在 2024–2025 被注册；Product Hunt 上已有同名工具。也容易被理解成\"只收 GitHub\"。" },
    { n: "Indiepapers", story: "独立发表的研究。", free: ["indiepapers.org"], taken: ["indiepapers.com"],
      con: "\"indie\" 可能让人觉得是业余作品，公司研究博客不像 indie。" },
    { n: "Researchposts", story: "直说：研究帖子。", free: ["researchposts.org", "researchposts.com"], taken: [],
      con: "最好搜，但最没个性，像一个分类目录。" }
  ];
  var dropped = "已排除（域名被占）：Findings（findings.pub 2026-07 注册）、Sidenote（sidenote.pub）、Fieldpost（fieldpost.org）、Paperstream（paperstream.org）、Webpapers（webpapers.org）。";
  var rows = N.map(function (x) {
    var free = x.free.map(function (d) { return '<span class="ok mono">✓ ' + d + '</span>'; }).join("<br>");
    var taken = x.taken.map(function (d) { return '<span class="no mono">✗ ' + d + '</span>'; }).join("<br>");
    return "<tr><td><b style='font-size:16px'>" + x.n + "</b>" + (x.tag ? " <span class='tag rec'>" + x.tag + "</span>" : "") + "</td><td>" + x.story + "</td><td>" + free + (taken ? "<br>" + taken : "") + "</td><td>" + x.con + "</td></tr>";
  }).join("");
  document.getElementById("names").innerHTML = "<table class='t'><tr><th>名字</th><th>含义</th><th>域名</th><th>顾虑</th></tr>" + rows + "</table><p class='note'>" + dropped +
    " 更正：上一轮说 gitpost.io 空闲是错的，RDAP 查不到 .io，改用 whois 查到它 2025-10-02 已被注册。</p>";
})();
