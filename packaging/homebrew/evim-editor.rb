class EvimEditor < Formula
  desc "Modal CLI text editor with Vim keybindings"
  homepage "https://github.com/tortr-rs/Editor-VIM"
  url "https://github.com/tortr-rs/Editor-VIM/archive/v1.0.0.tar.gz"
  sha256 "80667468d7ab7a7f41a21343444368aec09b34ec825012e5c67f6ec4303279ef"
  license "MIT"

  depends_on "python@3"

  def install
    libexec.install "evim.py"
    (bin/"evim").write <<~EOS
      #!/bin/sh
      exec "#{Formula["python@3"].opt_bin}/python3" "#{libexec}/evim.py" "$@"
    EOS
  end

  test do
    assert_match "EVim", shell_output("#{bin}/evim --version 2>&1", 1)
  end
end
