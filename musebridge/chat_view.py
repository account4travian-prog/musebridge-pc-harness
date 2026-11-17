"""Transcript rendering shared by dedicated chats and the Main companion."""
import html


class ChatPresentation:
    def show_message(self, identifier, role, text):
        self.rows[identifier] = (role, text[:65536])
        while len(self.rows) > 256:
            self.rows.popitem(last=False)
        self.render_messages()
        if role=='You':self.load_attachment()

    def confirm_manual_draft(self,cid,text):
        if cid==self.current_id:
            self.load_attachment()
            if self.input.toPlainText()==text:self.input.clear()

    def render_messages(self,force_scroll=False):
        from .presentation import markdown_fragment
        bar=self.chat.verticalScrollBar();position=bar.value();at_bottom=bar.maximum()-position<45
        blocks=[]
        from urllib.parse import quote
        for identifier,(role,text) in self.rows.items():
            body=markdown_fragment(text)
            attachment=self.worker.attachments.metadata(self.current_id,identifier) if role=='You' else None
            if attachment:
                body+='<p><a href="muse-image:'+quote(identifier,safe='')+'">Image: '+html.escape(attachment['filename'])+'</a></p>'
            color='#6d778b' if role=='You' else '#5179e8'
            blocks.append('<div style="margin-top:24px;margin-bottom:30px;"><p style="color:'+color+
                ';font-size:11px;font-weight:600;">'+html.escape(role)+'</p>'+body+'</div>')
        self.chat.setHtml('<html><head><style>body {font-family:"Segoe UI";font-size:14px;color:#262c36;} '
            'p {line-height:150%;margin-top:5px;margin-bottom:12px;} pre {background:#f4f6fa;white-space:pre-wrap;}'
            'code {font-family:Consolas;font-size:13px;color:#3f557d;} a {color:#5179e8;} '
            'h1,h2,h3 {color:#242b38;} </style></head><body>'+''.join(blocks)+'</body></html>')
        self.fit_transcript()
        if hasattr(self,'chat_stack'):self.chat_stack.setCurrentIndex(0 if self.rows else 1)
        bar.setValue(bar.maximum() if force_scroll or at_bottom else min(position,bar.maximum()))

    def fit_transcript(self):
        if not hasattr(self,'chat'):return
        frame=self.chat.document().rootFrame();fmt=frame.frameFormat()
        margin=max(28,(self.chat.viewport().width()-800)//2)
        fmt.setLeftMargin(margin);fmt.setRightMargin(margin);fmt.setTopMargin(10);fmt.setBottomMargin(24)
        frame.setFrameFormat(fmt)

