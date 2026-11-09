import os
os.environ["NO_COLOR"] = "1"   # stops bold() from adding terminal codes

import queue
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput

import main
from io_manager import OutputManager, InputManager

answers = queue.Queue()   # what the user typed, waiting for the coach
output_box = None    # the big box showing the coach's messages, modify fonts here
input_box = None          # the small box where the user types


class KivyOutput(OutputManager):
    
    def __init__(self,fitness_app):
        self.fitness_app = fitness_app

    def show_message(self, text):
        Clock.schedule_once(lambda dt: self.fitness_app.add_text(str(text)))


class KivyInput(InputManager):

    def __init__(self,fitness_app, out_put):
            self.fitness_app = fitness_app
            self.out_put = out_put

    def _read(self, prompt):
        Clock.schedule_once(lambda dt: self.fitness_app.add_text(prompt.rstrip()))
        return answers.get()   # waits until the user presses Send


class FitnessApp(App):
    def build(self):
        global output_box, input_box

        layout = BoxLayout(orientation="vertical", padding=10, spacing=10)

        output_box = TextInput(readonly=True, multiline=True)
        layout.add_widget(output_box)

        row = BoxLayout(size_hint_y=None, height=50, spacing=10)
        input_box = TextInput(multiline=False)
        input_box.bind(on_text_validate=self.send)   # Enter key also sends
        send_button = Button(text="Send", size_hint_x=0.25)
        send_button.bind(on_press=self.send)
        row.add_widget(input_box)
        row.add_widget(send_button)
        layout.add_widget(row)

        
        out = KivyOutput(self)
        main.use_io(out, KivyInput(self, out))
        threading.Thread(target=main.main, daemon=True).start()
        

        return layout

    def send(self, *args):
        text = input_box.text.strip()
        input_box.text = ""
        self.add_text("> " + text)
        answers.put(text)

    def add_text(self,text): #Adds text
        output_box.text += text + "\n"
        output_box.cursor = (0, output_box.text.count("\n"))   # scroll to the bottom


if __name__ == "__main__":
    FitnessApp().run()