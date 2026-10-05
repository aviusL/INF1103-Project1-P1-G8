from call_openai import ask_ai


print("Insert input here: ")


def initial_inputs():

    # AGE ------------------------
    while True:
        try:
            age = int(input("Age: "))
            if 13 <= age <= 100:
                break
            print("Please enter an age between 13 and 100.")
        except ValueError:
            print("Please enter a valid number.")

    # GENDER --------------------
    while True:
        gender = input("Gender: Male(M) or Female(F)?  ").strip().lower()

        if gender in ["male", "female", "m", "f"]:
            break

        print("Please enter male, female, or other.")

    # HEIGHT -----------------
    while True:
        try:
            height = float(input("Height (cm): "))

            if 100 <= height <= 250:
                break

            print("Please enter a height between 100 and 250.")
        except ValueError:
            print("Please enter a valid number.")

    # WEIGHT
    while True:
        try:
            weight = float(input("Weight (kg): "))

            if 30 <= weight <= 300:
                break

            print("Please enter a weight between 30 and 300 kg.")

        except ValueError:
            print("Please enter a valid number.")

    # FITNESS ------------------------
    while True:
        fitness = int(input("Fitness level, pick a number (1-beginner, 2-intermediate, 3-advanced): "))

        if fitness in [1,2,3]:
            break

        print("Please enter 1 - beginner, 2 - intermediate, or 3 - advanced.")

    # TRAINING GOAL --------------------
    training_goal = input("Training goal: ")

    # HEALTH OR INJURIES HISTORY -----------------
    while True:
        health_injury_history = input("Health/injury history (Nil or n for none): ").strip()

        if health_injury_history:
            break

        print("Please enter 'none' if you have no health or injury history.")

    # AVAILABLE EQUIPMENT -------------------
    avail_equipment = input("Available equipment: ")

    # AVAILABLE DAY AND TIMES
    avail_days = input("Available days and times: ")

    return age,gender,height,weight,fitness,training_goal,health_injury_history,avail_equipment,avail_days




def post_exercise_feedback():
    # PACE
    while True:
        try:
            pace = float(input("Average pace (min/km): "))

            if 2 <= pace <= 20:
                break

            print("Please enter a pace between 2 and 20 min/km.")

        except ValueError:
            print("Please enter a valid number.")


    # HEART RATE
    while True:
        try:
            heart_rate = int(input("Average heart rate (bpm): "))

            if 40 <= heart_rate <= 220:
                break

            print("Please enter a heart rate between 40 and 220 bpm.")

        except ValueError:
            print("Please enter a valid number.")


    # RPE
    while True:
        try:
            rpe = int(input("Rate of perceived exertion (1-10): "))

            if 1 <= rpe <= 10:
                break

            print("RPE must be between 1 and 10.")

        except ValueError:
            print("Please enter a whole number from 1 to 10.")


    # PAIN / DISCOMFORT
    pain = input(
        "Any pain or discomfort? "
    ).strip()

    if not pain:
        pain = "None"


    # COMMENTS
    comments = input(
        "How did the session feel? "
    ).strip()

    if not comments:
        comments = "No comments"


    return pace, heart_rate, rpe, pain, comments



# GET THE INPUTS ----------------------
(
    age,
    gender,
    height,
    weight,
    fitness,
    training_goal,
    health_injury_history,
    avail_equipment,
    avail_days
) = initial_inputs()



# POST EXERCISE INPUTS -------------------
(
    pace,heart_rate,rpe,pain,comments 
) = post_exercise_feedback()


# INITIAL PROMPT SETTINGS >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
prompt_initial = f"""
You are a fitness planning assistant.

Create a safe and realistic training plan based ONLY on the user's information below.

INITIAL USER INFORMATION
---------------
Age: {age}
Gender: {gender}
Height: {height}
Weight: {weight}
Fitness level: {fitness}

GOAL
----
{training_goal}

HEALTH AND INJURY HISTORY
-------------------------
{health_injury_history}

AVAILABLE EQUIPMENT
-------------------
{avail_equipment}

AVAILABILITY
------------
{avail_days}

INSTRUCTIONS
------------
1. Create a training plan that fits ONLY within the user's stated available days and times.
2. Respect the user's available equipment. Do not require equipment that is not available.
3. Take the user's injury history and current comments into account.
4. Do not prescribe exercises that could unnecessarily aggravate the reported injury or pain.
5. If an exercise may be unsuitable because of the user's injury history or current pain, replace it with a safer alternative.
6. The user's primary goal is the main training objective. Structure the plan around progressing toward that goal.
7. Do not assume the user has additional training time outside the stated availability.
8. Include rest or recovery when appropriate.
9. Keep the plan realistic for the user's current fitness level.
10. Do not diagnose injuries or medical conditions. If the reported pain could make training unsafe, clearly recommend seeking advice from a qualified healthcare professional.
11. fitness level numbers represents: 1 - beginner, 2 - intermediate, or 3 - advanced.

OUTPUT FORMAT
-------------
For each training session, provide:

Day:
Time:
Workout:
Duration:
Intensity:
Purpose:
Equipment:
Safety notes:

After the sessions, provide:
- Weekly goal
- Total planned training time
- Important safety considerations

Keep the response concise and practical.
"""

# POST EXERCISE PROMPT SETTING>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
post_exercise_prompt = f"""

Determine and feedback on user post-exercise data
pace {pace}, 
heart_rate {heart_rate}, 
rpe {rpe} , 
pain {pain}, 
comments {comments}


"""



# Get Input From User.

initial_result = ask_ai(prompt_initial)
print(initial_result)

# Post Exercise Feedback.
post_exercise_feedback()
post_result = ask_ai(post_exercise_prompt)
print(post_result)
