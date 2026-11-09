import os
os.environ["NO_COLOR"] = "1"   # stops bold() from adding terminal codes

import queue
import threading
import time
import kivy

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.core.window import Window
from kivy.utils import get_color_from_hex

import main
from io_manager import OutputManager, InputManager

# what the user typed, waiting for the coach
answers = queue.Queue()   


class KivyOutput(OutputManager):
    
    def __init__(self,fitness_app):
        self.fitness_app = fitness_app

    def show_message(self, text):
        Clock.schedule_once(lambda dt: self.fitness_app.screen.add_message(str(text)))


class KivyInput(InputManager):

    def __init__(self,fitness_app, out_put):
            self.fitness_app = fitness_app
            self.out_put = out_put

    def _read(self, prompt):
        Clock.schedule_once(lambda dt: self.fitness_app.screen.ask(prompt.rstrip()))
        return answers.get()   # waits until the user presses Send


class Screen:
    """Keeps output messages seperate in new screen"""

    def __init__(self, output_box, input_box):
        self.output_box = output_box
        self.input_box = input_box
        self.pending = []   # lines printed since the last question

    def show(self, text):
        """Replace everything in the big box with text."""
        self.output_box.text = text + "\n"
        Clock.schedule_once(self._scroll_to_top, 0.05)

    def _scroll_to_top(self, dt): #Scrolls to top automatically, prevent blank screens
        self.output_box.cursor = (0, 0)
        self.output_box.scroll_y = 1

    def add_message(self, text):
        """Add a line to the current screen and show it."""
        self.pending.append(text)
        self.show("\n".join(self.pending))

    def ask(self, prompt):
        """Show the coach's question as the last line, then wait for typing."""
        self.show("\n".join(self.pending + [prompt]))
        self.pending.clear()
        self.input_box.focus = True


class FitnessApp(App):

    #settings to modify text, from folder gui_decorators
    FONT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),"gui_decorators", "Montserrat-ExtraBoldItalic.ttf")

    def build(self):
        global output_box, input_box

        layout = BoxLayout(orientation="vertical", padding=10, spacing=10)

        #this settings changes how output texts looks
        output_box = TextInput(readonly=True, 
                               multiline=True,
                               foreground_color= get_color_from_hex("#D8C515"),
                               background_color= get_color_from_hex("#195308"),
                               font_name = self.FONT_FILE
                               )
        
        layout.add_widget(output_box)

        row = BoxLayout(size_hint_y=None, height=50, spacing=10)
        input_box = TextInput(multiline=False)
        input_box.bind(on_text_validate=self.send)   # Enter key sends

        row.add_widget(input_box) # Adds input typing box
        layout.add_widget(row)

        self.screen = Screen(output_box, input_box)
        
        out = KivyOutput(self)
        main.use_io(out, KivyInput(self, out))
        threading.Thread(target=self.run_coach, daemon=True).start()
        

        return layout

    def send(self, *args):
        text = input_box.text.strip()
        input_box.text = ""
        self.add_text("> " + text)
        answers.put(text)

    def add_text(self,text): #Adds text
        output_box.text += text + "\n"
        output_box.cursor = (0, output_box.text.count("\n"))   # scroll to the bottom
        input_box.focus = True # user cursors go back  to typing box, so no need click again after every input.

    def run_coach(self):
        """Allows app window to close automatically after user presses 6. """
        main.main()
        time.sleep(0.8) #Time set before closing window
        Clock.schedule_once(lambda dt: App.get_running_app().stop())


if __name__ == "__main__":
    Window.size = (550, 700) #Adjusts size of window
    FitnessApp().run()